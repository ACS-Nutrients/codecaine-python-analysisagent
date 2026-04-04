"""
RAGAS Faithfulness 테스트
- 에이전트 key_concerns/reason이 KB(약물 상호작용 252개)에 근거했는지 측정
- 대상: G 페르소나 + EG 케이스

사용법:
  python scripts/run_ragas_test.py --results scripts/test_results_g.json --kb lpi_kb_texts.json
  (기존 boto3 AWS 자격증명 사용 — 별도 API 키 불필요)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ragas import evaluate
from ragas.metrics import faithfulness
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_aws import ChatBedrock, BedrockEmbeddings
from datasets import Dataset


def load_kb(kb_path: str) -> list[str]:
    """KB 문서 로드"""
    kb = json.loads(Path(kb_path).read_text(encoding="utf-8"))
    return kb["documents"]


def extract_claims(agent_output: dict) -> str:
    """에이전트 출력에서 평가할 텍스트 추출 (key_concerns + reason 필드)"""
    step1 = agent_output.get("step1", {})
    summary = step1.get("summary", {})

    claims = []

    # key_concerns
    for concern in summary.get("key_concerns", []):
        claims.append(str(concern))

    # required_nutrients reason
    for nutrient in step1.get("required_nutrients", []):
        reason = nutrient.get("reason", "")
        if "[약물 상호작용]" in reason or "[혈액검사 근거]" in reason:
            claims.append(f"{nutrient['name_ko']}: {reason}")

    return " | ".join(claims) if claims else "no claims"


def find_relevant_context(medications: list[str], kb_docs: list[str]) -> list[str]:
    """
    사용자 약물 목록 기반으로 KB에서 관련 문서 검색 (키워드 매칭)
    약물명 영문/한글 매핑
    """
    med_keywords = {
        "아토르바스타틴": ["statin", "atorvastatin", "coq10", "CoQ10"],
        "스타틴": ["statin", "coq10", "CoQ10"],
        "메트포르민": ["metformin", "vitamin b12", "B12"],
        "암로디핀": ["amlodipine", "calcium channel", "magnesium"],
        "와파린": ["warfarin", "vitamin k", "anticoagul"],
        "혈압약": ["antihypertensive", "blood pressure", "magnesium"],
    }

    keywords = set()
    for med in medications:
        for key, terms in med_keywords.items():
            if key in med or med.lower() in key:
                keywords.update(terms)

    if not keywords:
        # 약물 없으면 일반 영양소 문서 반환
        return kb_docs[:5]

    relevant = []
    for doc in kb_docs:
        doc_lower = doc.lower()
        if any(kw.lower() in doc_lower for kw in keywords):
            relevant.append(doc)

    return relevant[:10] if relevant else kb_docs[:5]


def build_ragas_dataset(results_path: str, kb_docs: list[str]) -> Dataset:
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))

    questions, answers, contexts = [], [], []

    for r in results:
        if "error" in r or not r.get("agent_output"):
            continue

        case_input = r.get("input", {})
        agent_output = r.get("agent_output", {})
        user_profile = case_input.get("user_profile", {})
        medications = user_profile.get("medications", [])

        # question: 사용자 약물 + 섭취 목적
        question = (
            f"약물 복용: {', '.join(medications) if medications else '없음'}. "
            f"섭취 목적: {', '.join(user_profile.get('intake_purpose', []))}"
        )

        # answer: 에이전트의 약물 관련 주장
        answer = extract_claims(agent_output)

        # context: KB에서 관련 문서
        context = find_relevant_context(medications, kb_docs)

        questions.append(question)
        answers.append(answer)
        contexts.append(context)

    return Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts,
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="scripts/test_results_g.json", help="에이전트 결과 JSON")
    parser.add_argument("--kb", default="lpi_kb_texts.json", help="KB 문서 JSON")
    parser.add_argument("--output", default="scripts/ragas_results.json")
    args = parser.parse_args()

    print("KB 로드 중...")
    kb_docs = load_kb(args.kb)
    print(f"KB 문서: {len(kb_docs)}개")

    # 에이전트 결과에 input 추가 (golden_set에서 매칭)
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    golden = json.loads(Path("scripts/golden_set_g.json").read_text(encoding="utf-8"))
    golden_map = {c["case_id"]: c["input"] for c in golden}
    for r in results:
        r["input"] = golden_map.get(r.get("case_id"), {})

    Path(args.results).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RAGAS 데이터셋 구성 중...")
    dataset = build_ragas_dataset(args.results, kb_docs)
    print(f"평가 샘플: {len(dataset)}개")

    if len(dataset) == 0:
        print("평가할 데이터 없음. test_results_g.json에 agent_output이 있는지 확인하세요.")
        return

    # LLM 설정 (AWS Bedrock Claude — 기존 boto3 자격증명 사용)
    llm = LangchainLLMWrapper(ChatBedrock(
        model_id="anthropic.claude-haiku-4-5-20251001:0",
        region_name="ap-northeast-2",
    ))

    print("\nRAGAS Faithfulness 측정 중...\n")
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness],
        llm=llm,
    )

    scores = result.to_pandas()
    print(scores[["question", "faithfulness"]].to_string())

    avg = scores["faithfulness"].mean()
    print(f"\n평균 Faithfulness: {avg:.3f}")
    print("해석: 1.0 = 모든 주장이 KB에 근거 | 0.0 = KB 근거 없음")

    output = {
        "avg_faithfulness": round(float(avg), 3),
        "details": scores[["question", "faithfulness"]].to_dict(orient="records"),
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장됨: {args.output}")


if __name__ == "__main__":
    main()
