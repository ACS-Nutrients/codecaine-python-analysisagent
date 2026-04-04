"""
Faithfulness 테스트 (수정본)

핵심 수정:
- Cohere Embed v4 모델 ID 수정: cohere.embed-v4:0
- Embed 요청 포맷 보강: embedding_types, truncate=RIGHT
- KB 문서 임베딩 1회 캐시
- keyword fallback 강화
- medication 추출 경로 보강
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import boto3

REGION = "ap-northeast-2"
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBED_MODEL_ID = "global.cohere.embed-v4:0"

FAITHFULNESS_PROMPT = """주어진 Context(참고 문서)와 Claims(에이전트 주장 목록)를 보고,
각 Claim이 Context에서 지지되는지 판단하세요.

Context:
{context}

Claims:
{claims}

각 Claim에 대해 아래 형식으로 JSON 배열만 출력하세요. 다른 텍스트 없이.
[
  {{"claim": "claim 원문", "supported": true}},
  ...
]

supported 기준:
- true: Context에 해당 주장을 뒷받침하는 내용이 있음
- false: Context에 근거 없거나 반대되는 내용임
"""


# -----------------------------
# Bedrock 호출
# -----------------------------
def call_bedrock(prompt: str, bedrock_client) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()


def load_kb(kb_path: str) -> list[str]:
    kb = json.loads(Path(kb_path).read_text(encoding="utf-8"))
    return kb["documents"]


# -----------------------------
# Claim 추출
# -----------------------------
def extract_claims(agent_output: dict) -> list[str]:
    step1 = agent_output.get("step1", {})
    summary = step1.get("summary", {})
    claims: list[str] = []

    for concern in summary.get("key_concerns", []) or []:
        claims.append(str(concern))

    for nutrient in step1.get("required_nutrients", []) or []:
        reason = str(nutrient.get("reason", ""))
        if "[약물 상호작용]" in reason or "[혈액검사 근거]" in reason:
            claims.append(f"{nutrient.get('name_ko', '')}: {reason}")

    return claims


# -----------------------------
# 임베딩
# -----------------------------
def embed_texts(
    texts: list[str],
    bedrock_client,
    input_type: str = "search_document",
) -> list[list[float]]:
    """
    Cohere Embed v4 for Bedrock.
    문서 기준:
    - modelId: cohere.embed-v4:0
    - texts 최대 96개
    - input_type: search_document / search_query ...
    """
    if not texts:
        return []

    BATCH_SIZE = 96
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        body = {
            "input_type": input_type,
            "texts": batch,
            "embedding_types": ["float"],
            "truncate": "RIGHT",
        }

        response = bedrock_client.invoke_model(
            modelId=EMBED_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())

        # 문서 예시 기준: embeddings.float
        embeddings = result.get("embeddings", {}).get("float")
        if not embeddings:
            raise ValueError(f"Unexpected embedding response shape: {result}")

        all_embeddings.extend(embeddings)

    return all_embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b + 1e-9)


# -----------------------------
# 정규화 / fallback
# -----------------------------
_ALIAS_MAP = {
    "비타민d": ["vitamin d", "vitamind", "비타민 d", "비타민d3", "vitamin d3", "d3"],
    "코엔자임q10": ["coq10", "coenzyme q10", "coenzymeq10", "코큐텐", "유비퀴논"],
    "아토르바스타틴": ["atorvastatin", "스타틴", "statin", "아토르바스타틴"],
    "메트포르민": ["metformin", "메트포르민"],
    "암로디핀": ["amlodipine", "암로디핀"],
    "마그네슘": ["magnesium", "마그네슘"],
    "비타민b12": ["vitamin b12", "b12", "비타민 b12", "코발라민"],
    "오메가3": ["omega-3", "omega3", "epa", "dha", "오메가-3", "오메가3"],
    "중성지방": ["triglyceride", "triglycerides", "tg", "중성지방"],
}

_STOPWORDS = {
    "가능", "주의", "필요", "보충", "권장", "감소", "저하", "상승", "경계값",
    "복용", "중", "및", "으로", "인한", "위험", "수치", "정상", "미만", "초과",
    "the", "and", "or", "with", "for", "from", "중이", "해당", "근거", "혈액검사",
    "약물", "상호작용",
}


def normalize_text(text: str) -> str:
    t = str(text).lower().strip()
    t = t.replace("(", " ").replace(")", " ").replace("/", " ").replace(",", " ")
    t = t.replace("—", " ").replace("-", "")
    t = re.sub(r"\s+", " ", t)
    return t


def expand_aliases(text: str) -> str:
    t = normalize_text(text)
    expanded = [t]
    for canon, aliases in _ALIAS_MAP.items():
        candidates = [canon] + aliases
        if any(a in t for a in candidates):
            expanded.extend(candidates)
    return " ".join(expanded)


def extract_keywords(text: str) -> list[str]:
    t = expand_aliases(text)
    tokens = re.findall(r"[a-zA-Z0-9가-힣\.]+", t)
    result = []
    for tok in tokens:
        if len(tok) <= 1:
            continue
        if tok in _STOPWORDS:
            continue
        result.append(tok)
    return list(dict.fromkeys(result))


def keyword_fallback(query: str, kb_docs: list[str], top_k: int = 6) -> list[str]:
    keywords = extract_keywords(query)
    if not keywords:
        return kb_docs[:top_k]

    scored = []
    for doc in kb_docs:
        doc_norm = expand_aliases(doc)
        score = 0
        for kw in keywords:
            if kw in doc_norm:
                score += 1
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [doc for _, doc in scored[:top_k]]
    return results if results else kb_docs[:top_k]


# -----------------------------
# KB 검색
# -----------------------------
def find_relevant_context(
    query: str,
    kb_docs: list[str],
    bedrock_client,
    kb_doc_vecs: list[list[float]] | None = None,
    top_k: int = 6,
) -> list[str]:
    """
    임베딩 우선, 실패 시 alias-aware keyword fallback
    """
    try:
        query_vec = embed_texts([query], bedrock_client, input_type="search_query")[0]

        if kb_doc_vecs is None:
            kb_doc_vecs = embed_texts(kb_docs, bedrock_client, input_type="search_document")

        scored = sorted(
            enumerate(kb_doc_vecs),
            key=lambda x: cosine_similarity(query_vec, x[1]),
            reverse=True,
        )
        return [kb_docs[i] for i, _ in scored[:top_k]]
    except Exception as e:
        print(f"    [EMBED ERROR] {e} — keyword fallback 사용")
        return keyword_fallback(query, kb_docs, top_k=top_k)


# -----------------------------
# Faithfulness scoring
# -----------------------------
def score_faithfulness(claims: list[str], context_docs: list[str], bedrock_client) -> tuple[float, list[dict]]:
    if not claims:
        return 1.0, []

    context_text = "\n\n---\n\n".join(context_docs[:5])
    claims_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))
    prompt = FAITHFULNESS_PROMPT.format(context=context_text[:5000], claims=claims_text)

    try:
        raw = call_bedrock(prompt, bedrock_client)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return 0.0, []
        results = json.loads(match.group())
        supported = sum(1 for r in results if r.get("supported"))
        score = supported / len(results) if results else 0.0
        return round(score, 3), results
    except Exception as e:
        print(f"    [ERROR] {e}")
        return 0.0, []


# -----------------------------
# 입력 보조
# -----------------------------
def extract_medications(case_input: dict) -> list[str]:
    meds: list[str] = []

    user_profile = case_input.get("user_profile", {}) or {}
    medication_info = case_input.get("medication_info", []) or []

    if isinstance(user_profile.get("medications"), list):
        meds.extend([str(x) for x in user_profile["medications"] if x])

    if isinstance(medication_info, list):
        for m in medication_info:
            if isinstance(m, dict):
                meds.extend(
                    [
                        str(m.get("name", "")),
                        str(m.get("drug_name", "")),
                        str(m.get("product_name", "")),
                    ]
                )
            elif m:
                meds.append(str(m))

    meds = [m.strip() for m in meds if m and str(m).strip()]
    return list(dict.fromkeys(meds))


# -----------------------------
# main
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="scripts/test_results_g.json")
    parser.add_argument("--kb", default="lpi_kb_texts.json")
    parser.add_argument("--golden", default="scripts/golden_set_g.json")
    parser.add_argument("--output", default="scripts/ragas_results.json")
    args = parser.parse_args()

    bedrock = boto3.client("bedrock-runtime", region_name=REGION)

    print("KB 로드 중...")
    kb_docs = load_kb(args.kb)
    print(f"KB 문서: {len(kb_docs)}개")

    print("KB 임베딩 캐시 생성 중...")
    kb_doc_vecs = None
    try:
        kb_doc_vecs = embed_texts(kb_docs, bedrock, input_type="search_document")
        print(f"KB 임베딩 완료: {len(kb_doc_vecs)}개")
    except Exception as e:
        print(f"[WARN] KB 임베딩 사전 생성 실패: {e}")
        print("[WARN] 각 케이스별 fallback 또는 재시도 사용")

    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    golden_map = {c["case_id"]: c["input"] for c in golden}

    print(f"\n{'='*60}")
    print(f"{'케이스':<10} {'Claims':>8} {'Faithfulness':>14} {'판정':>8}")
    print(f"{'-'*50}")

    all_scores = []

    for r in results:
        case_id = r.get("case_id", "?")
        agent_output = r.get("agent_output", {})
        if not agent_output:
            print(f"{case_id:<10} {'N/A':>8}")
            continue

        inp = golden_map.get(case_id, {})
        medications = extract_medications(inp)

        claims = extract_claims(agent_output)
        query = " ".join(medications) + " " + " ".join(claims[:3])

        context = find_relevant_context(
            query=query,
            kb_docs=kb_docs,
            bedrock_client=bedrock,
            kb_doc_vecs=kb_doc_vecs,
        )

        print(f"{case_id:<10} {len(claims):>8}개  ", end="", flush=True)
        score, detail = score_faithfulness(claims, context, bedrock)

        icon = "🟢" if score >= 0.8 else ("🟡" if score >= 0.5 else "🔴")
        print(f"{score:.3f} ({int(score*100)}%)  {icon}")

        for d in detail:
            if not d.get("supported"):
                print(f"    ✗ {d.get('claim','')[:120]}")

        all_scores.append(
            {
                "case_id": case_id,
                "faithfulness": score,
                "num_claims": len(claims),
                "detail": detail,
                "retrieved_context_preview": context[:3],
            }
        )

    if all_scores:
        avg = sum(s["faithfulness"] for s in all_scores) / len(all_scores)
        print(f"\n{'='*60}")
        print(f"평균 Faithfulness: {avg:.3f} ({int(avg*100)}%)")
        print("해석: 1.0 = 모든 주장이 KB에 근거 | 0.0 = KB 근거 없음")

        output = {
            "avg_faithfulness": round(avg, 3),
            "details": all_scores,
        }
        Path(args.output).write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"결과 저장됨: {args.output}")


if __name__ == "__main__":
    main()