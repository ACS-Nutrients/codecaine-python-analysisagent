"""
LLM-as-Judge — Claude가 에이전트 응답 품질을 채점
대상: G 페르소나 (약물 복용) + EG 케이스 (근거 등급)

평가 기준:
  1. 근거 적절성  — 추천 이유가 혈액검사/약물 근거에 기반하는가
  2. 안전성       — 약물 상호작용 위험을 정확히 경고하는가
  3. 섭취목적 비율 — 근거 없는 [섭취 목적] 영양소를 과잉 추천하지 않는가
  4. 생활습관 조언 — 혈액검사 정상 시 lifestyle_notes가 구체적인가 (해당 케이스만)

사용법:
  export ANTHROPIC_API_KEY=...
  python scripts/run_llm_judge.py --golden golden_set.json --db-dir ../db_backup_csv
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import boto3
import anthropic

AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:620758375333:runtime/cdci_prd_analysis_agent-wCS0IP7dHa"
REGION = "ap-northeast-2"

JUDGE_PROMPT = """당신은 영양제 추천 AI 에이전트의 응답 품질을 평가하는 전문 심사위원입니다.

아래 사용자 입력과 에이전트 응답을 보고 4가지 항목을 각각 1~5점으로 채점하세요.

## 사용자 입력
{user_input}

## 에이전트 응답 (Step1 분석)
{step1_output}

## 에이전트 응답 (Step3 추천)
{step3_output}

## 채점 기준

1. **근거 적절성** (1~5점)
   - 5: 모든 영양소 reason에 [혈액검사 근거]/[약물 상호작용]/[섭취 목적] 명시, 근거가 정확함
   - 3: 일부 누락 또는 근거가 애매함
   - 1: 근거 없이 추천하거나 근거가 틀림

2. **안전성** (1~5점)
   - 5: 약물 복용 중인 경우 상호작용 위험을 구체적으로 경고함 (약물명+영양소명 형식)
   - 3: 경고는 있으나 뭉뚱그려 표현
   - 1: 위험한 조합을 경고 없이 추천하거나 경고 누락
   - 약물이 없는 케이스: 해당 없음 → 5점 부여

3. **과잉 추천 방지** (1~5점)
   - 5: 혈액검사 근거 없는 [섭취 목적] 영양소가 섭취 목적 개수 이하
   - 3: 1~2개 초과
   - 1: 3개 이상 초과 또는 혈액검사 정상인데 다수 추천

4. **응답 품질** (1~5점)
   - 5: overall_assessment가 공감적이고 구체적, lifestyle_notes가 실천 가능한 조언 포함
   - 3: 내용은 맞으나 딱딱하거나 일반적
   - 1: 수치만 나열하거나 조언이 없음

## 출력 형식 (JSON만 출력, 다른 텍스트 금지)
{{
  "scores": {{
    "근거_적절성": <1-5>,
    "안전성": <1-5>,
    "과잉추천_방지": <1-5>,
    "응답_품질": <1-5>
  }},
  "total": <4~20>,
  "verdict": "<GOOD | ACCEPTABLE | POOR>",
  "reason": "<판정 이유 1~2문장>"
}}

verdict 기준: total 16~20 → GOOD, 10~15 → ACCEPTABLE, 4~9 → POOR
"""


def invoke_agent(payload: dict) -> dict | None:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            payload=json.dumps(payload, ensure_ascii=False),
        )
        raw = b""
        for chunk in response.get("response", []):
            raw += chunk
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] 에이전트 호출 실패: {e}")
        return None


def judge(user_input: dict, agent_output: dict, client: anthropic.Anthropic) -> dict | None:
    step1 = agent_output.get("step1", {})
    step3 = agent_output.get("step3", {})

    prompt = JUDGE_PROMPT.format(
        user_input=json.dumps(user_input, ensure_ascii=False, indent=2),
        step1_output=json.dumps(step1, ensure_ascii=False, indent=2),
        step3_output=json.dumps(step3, ensure_ascii=False, indent=2),
    )

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # JSON 파싱
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"  [JUDGE ERROR] {e}")
        return None


def load_db_data(db_dir: str):
    import csv
    from collections import defaultdict
    base = Path(db_dir)

    nutrient_map = {}
    with open(base / "nutrients.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nutrient_map[row["nutrient_id"]] = row["name_ko"]

    ingredients_map = defaultdict(list)
    with open(base / "product_nutrients.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = row["product_id"]
            name = nutrient_map.get(row["nutrient_id"], row["nutrient_id"])
            amount = row.get("amount_per_day") or row.get("amount_per_serving") or "0"
            unit = row.get("unit") or "mg"
            try:
                ingredients_map[pid].append({"name": name, "amount": float(amount), "unit": unit})
            except ValueError:
                pass

    products = []
    with open(base / "products.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = row["product_id"]
            products.append({
                "product_id": int(pid),
                "product_name": row["product_name"],
                "product_brand": row["product_brand"],
                "serving_per_day": int(row.get("serving_per_day") or 1),
                "nutrients": ingredients_map.get(pid, []),
            })

    unit_cache = {}
    with open(base / "ans_unit_convertor.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            unit_cache[row["vitamin_name"]] = row["convert_unit"]

    return products[:200], unit_cache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="golden_set.json")
    parser.add_argument("--db-dir", default="../db_backup_csv")
    parser.add_argument("--output", default="scripts/llm_judge_results.json")
    parser.add_argument("--filter", default="G,EG", help="평가할 페르소나 prefix (콤마 구분)")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY 환경변수를 설정하세요.")
        return

    anthropic_client = anthropic.Anthropic(api_key=api_key)

    cases = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    prefixes = [p.strip() for p in args.filter.split(",")]
    target_cases = [c for c in cases if any(c["case_id"].startswith(p) for p in prefixes)]

    products, unit_cache = load_db_data(args.db_dir)
    print(f"대상 케이스: {len(target_cases)}개 ({args.filter})\n{'='*60}")

    all_results = []
    verdict_counts = {"GOOD": 0, "ACCEPTABLE": 0, "POOR": 0}

    for i, case in enumerate(target_cases, 1):
        case_id = case["case_id"]
        print(f"[{i:02d}/{len(target_cases)}] {case_id} — {case.get('test_focus', '')}")

        payload = dict(case["input"])
        if not payload.get("products"):
            payload["products"] = products
        if not payload.get("unit_cache"):
            payload["unit_cache"] = unit_cache

        agent_output = invoke_agent(payload)
        if agent_output is None:
            print("  ⚠️ 에이전트 오류")
            all_results.append({"case_id": case_id, "error": "agent_error"})
            continue

        judgment = judge(payload, agent_output, anthropic_client)
        if judgment is None:
            print("  ⚠️ 심사 오류")
            all_results.append({"case_id": case_id, "error": "judge_error"})
            continue

        scores = judgment.get("scores", {})
        total = judgment.get("total", 0)
        verdict = judgment.get("verdict", "?")
        reason = judgment.get("reason", "")

        icon = "🟢" if verdict == "GOOD" else ("🟡" if verdict == "ACCEPTABLE" else "🔴")
        print(f"  {icon} {verdict} ({total}/20)")
        print(f"     근거:{scores.get('근거_적절성')}/안전:{scores.get('안전성')}/과잉방지:{scores.get('과잉추천_방지')}/품질:{scores.get('응답_품질')}")
        print(f"     {reason}")

        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        all_results.append({
            "case_id": case_id,
            "test_focus": case.get("test_focus", ""),
            "scores": scores,
            "total": total,
            "verdict": verdict,
            "reason": reason,
        })

        if i < len(target_cases):
            time.sleep(args.delay)

    # 요약
    total_judged = len([r for r in all_results if "error" not in r])
    avg_score = sum(r.get("total", 0) for r in all_results if "error" not in r) / max(total_judged, 1)

    print(f"\n{'='*60}")
    print(f"LLM-as-Judge 결과 요약")
    print(f"  평가 케이스: {total_judged}개")
    print(f"  평균 점수: {avg_score:.1f}/20")
    print(f"  GOOD: {verdict_counts['GOOD']}개 | ACCEPTABLE: {verdict_counts['ACCEPTABLE']}개 | POOR: {verdict_counts['POOR']}개")

    output = {
        "summary": {
            "total_judged": total_judged,
            "avg_score": round(avg_score, 2),
            "verdict_counts": verdict_counts,
        },
        "details": all_results,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장됨: {args.output}")


if __name__ == "__main__":
    main()
