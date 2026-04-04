"""
골든셋 테스트 러너
사용법: python scripts/run_golden_set_test.py --golden golden_set.json --db-dir ../db_backup_csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import boto3

AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:620758375333:runtime/cdci_prd_analysis_agent-wCS0IP7dHa"
REGION = "ap-northeast-2"


# ── DB CSV 로딩 ──────────────────────────────────────────────────

def load_db_data(db_dir: str) -> tuple[list[dict], dict]:
    """
    products.csv + product_nutrients.csv + nutrients.csv → products 리스트
    ans_unit_convertor.csv → unit_cache dict
    """
    base = Path(db_dir)

    # 영양소 id → name_ko 매핑
    nutrient_map: dict[str, str] = {}
    with open(base / "nutrients.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nutrient_map[row["nutrient_id"]] = row["name_ko"]

    # product_id → ingredients 매핑
    ingredients_map: dict[str, list[dict]] = {}
    with open(base / "product_nutrients.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = row["product_id"]
            name = nutrient_map.get(row["nutrient_id"], row["nutrient_id"])
            amount = row.get("amount_per_day") or row.get("amount_per_serving") or "0"
            unit = row.get("unit") or "mg"
            if pid not in ingredients_map:
                ingredients_map[pid] = []
            try:
                ingredients_map[pid].append({
                    "name": name,
                    "amount": float(amount),
                    "unit": unit,
                })
            except ValueError:
                pass

    # products 리스트 조립
    products: list[dict] = []
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

    # unit_cache 조립
    unit_cache: dict[str, str] = {}
    with open(base / "ans_unit_convertor.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            unit_cache[row["vitamin_name"]] = row["convert_unit"]

    print(f"[DB] products: {len(products)}개, unit_cache: {len(unit_cache)}개 항목 로드")
    return products, unit_cache


# ── 에이전트 호출 ────────────────────────────────────────────────

def invoke_agent(input_payload: dict) -> dict | None:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            payload=json.dumps(input_payload, ensure_ascii=False),
        )
        raw = b""
        for chunk in response.get("response", []):
            raw += chunk
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] 에이전트 호출 실패: {e}")
        return None


# ── pass/fail 판정 ───────────────────────────────────────────────

def extract_nutrients(step1: dict) -> list[str]:
    """step1.required_nutrients의 name_ko 목록 반환"""
    return [
        n.get("name_ko", "") or ""
        for n in step1.get("required_nutrients", [])
    ]

def extract_warnings(step1: dict) -> list[str]:
    """step1.summary의 risk_warnings + key_concerns 합산"""
    summary = step1.get("summary", {})
    warnings = summary.get("risk_warnings", []) or []
    concerns = summary.get("key_concerns", []) or []
    return [str(w) for w in warnings + concerns]

def extract_recommendation_names(step3: dict) -> list[str]:
    """step3.recommendations의 product_name 목록"""
    return [r.get("product_name", "") for r in step3.get("recommendations", [])]


def keywords_from_check(check_str: str) -> tuple[list[str], bool]:
    """
    check 문자열에서 키워드와 must_not 여부 파싱.
    예: "step1.required_nutrients contains '비타민 D' or '비타민D'" → (['비타민 D','비타민D'], False)
    예: "step3.recommendations must NOT include '나이아신'" → (['나이아신'], True)
    """
    must_not = "must NOT" in check_str or "must not" in check_str
    tokens = re.findall(r"'([^']+)'", check_str)
    return tokens, must_not


def check_rule(check_str: str, agent_output: dict) -> bool:
    """
    check 문자열을 파싱해서 에이전트 출력 대상 필드에서 pass/fail 판정.
    - 키워드 포함 여부 (기본)
    - "개수가 N개 초과하면 위반" 패턴 지원
    """
    step1 = agent_output.get("step1", {})
    step3 = agent_output.get("step3", {})

    # ── 개수 비교 패턴: "X 개수가 N개 초과하면 위반" ──────────────
    count_match = re.search(r'required_nutrients\s+전체\s+개수가\s+(\d+)개\s+초과하면\s+위반', check_str)
    if count_match:
        limit = int(count_match.group(1))
        actual = len(step1.get("required_nutrients", []))
        return actual <= limit  # 초과하면 위반 → 초과 안 하면 pass

    keywords, must_not = keywords_from_check(check_str)
    if not keywords:
        return True  # 키워드 없으면 체크 불가 → pass 처리

    check_lower = check_str.lower()

    # 대상 필드 결정
    if "step1.required_nutrients" in check_lower:
        haystack = " ".join(extract_nutrients(step1))
    elif "step1.summary.risk_warnings" in check_lower or "key_concerns" in check_lower or "risk_warnings" in check_lower:
        haystack = " ".join(extract_warnings(step1))
    elif "step3.recommendations" in check_lower or "step3" in check_lower:
        haystack = " ".join(extract_recommendation_names(step3))
    elif "step1" in check_lower:
        # step1 전체 텍스트에서 검색
        haystack = json.dumps(step1, ensure_ascii=False)
    else:
        haystack = json.dumps(agent_output, ensure_ascii=False)

    found = any(kw in haystack for kw in keywords)

    if must_not:
        return not found   # must NOT → 없어야 pass
    else:
        return found       # contains → 있어야 pass


def evaluate_case(case: dict, agent_output: dict) -> dict:
    """단일 케이스 pass/fail 평가. 결과 dict 반환."""
    results = {
        "case_id": case["case_id"],
        "test_focus": case.get("test_focus", ""),
        "rule_results": [],
        "safety_results": [],
        "overall": "PASS",
    }

    # rules_violated_if_missing 체크
    for rule in case["expected_output"].get("rules_violated_if_missing", []):
        passed = check_rule(rule["check"], agent_output)
        results["rule_results"].append({
            "rule": rule["rule"][:80],
            "passed": passed,
        })
        if not passed:
            results["overall"] = "FAIL"

    # must_not_occur 체크
    for rule in case["expected_output"].get("must_not_occur", []):
        passed = check_rule(rule["check"], agent_output)
        results["safety_results"].append({
            "violation": rule["violation"][:80],
            "passed": passed,
        })
        if not passed:
            results["overall"] = "FAIL"

    return results


# ── 메인 ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="golden_set.json", help="골든셋 파일 경로")
    parser.add_argument("--output", default="test_results.json", help="결과 저장 경로")
    parser.add_argument("--db-dir", default="../db_backup_csv", help="DB CSV 디렉토리 경로")
    parser.add_argument("--delay", type=float, default=1.0, help="케이스 간 호출 간격(초)")
    args = parser.parse_args()

    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"골든셋 파일 없음: {golden_path}")
        return

    # DB 데이터 로드 (products는 상위 200개만 — payload 크기 제한)
    products, unit_cache = load_db_data(args.db_dir)
    products = products[:200]

    cases = json.loads(golden_path.read_text(encoding="utf-8"))
    print(f"총 {len(cases)}개 케이스 테스트 시작\n{'='*60}")

    all_results = []
    pass_count = 0

    for i, case in enumerate(cases, 1):
        case_id = case["case_id"]
        print(f"[{i:02d}/{len(cases)}] {case_id} — {case.get('test_focus','')}")

        # DB 데이터를 input에 주입 (골든셋에 없는 경우만)
        payload = dict(case["input"])
        if not payload.get("products"):
            payload["products"] = products
        if not payload.get("unit_cache"):
            payload["unit_cache"] = unit_cache

        agent_output = invoke_agent(payload)
        if agent_output is None:
            result = {
                "case_id": case_id,
                "test_focus": case.get("test_focus", ""),
                "overall": "ERROR",
                "rule_results": [],
                "safety_results": [],
            }
        else:
            result = evaluate_case(case, agent_output)
            result["agent_output"] = agent_output  # 원본 저장

        status = result["overall"]
        icon = "✅" if status == "PASS" else ("❌" if status == "FAIL" else "⚠️")
        print(f"  {icon} {status}")

        # 실패한 규칙만 출력
        for r in result.get("rule_results", []):
            if not r["passed"]:
                print(f"     FAIL — {r['rule']}")
        for r in result.get("safety_results", []):
            if not r["passed"]:
                print(f"     SAFETY FAIL — {r['violation']}")

        all_results.append(result)
        if status == "PASS":
            pass_count += 1

        if i < len(cases):
            time.sleep(args.delay)

    # 결과 저장
    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 요약 출력
    total = len(cases)
    fail_count = sum(1 for r in all_results if r["overall"] == "FAIL")
    error_count = sum(1 for r in all_results if r["overall"] == "ERROR")

    print(f"\n{'='*60}")
    print(f"결과 요약")
    print(f"  전체: {total}개")
    print(f"  PASS: {pass_count}개 ({pass_count/total*100:.1f}%)")
    print(f"  FAIL: {fail_count}개")
    print(f"  ERROR: {error_count}개")

    # 페르소나별 pass율
    persona_stats: dict[str, dict] = {}
    for r in all_results:
        p = r["case_id"][0]
        if p not in persona_stats:
            persona_stats[p] = {"total": 0, "pass": 0}
        persona_stats[p]["total"] += 1
        if r["overall"] == "PASS":
            persona_stats[p]["pass"] += 1

    print("\n페르소나별 pass율:")
    for p, s in sorted(persona_stats.items()):
        pct = s["pass"] / s["total"] * 100
        print(f"  Persona {p}: {s['pass']}/{s['total']} ({pct:.0f}%)")

    print(f"\n상세 결과 저장됨: {output_path}")


if __name__ == "__main__":
    main()
