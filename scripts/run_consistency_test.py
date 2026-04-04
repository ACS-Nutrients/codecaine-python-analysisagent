"""
일관성 테스트 — 동일 케이스를 N회 반복 실행해 pass율 분산 측정
사용법: python scripts/run_consistency_test.py --golden golden_set.json --db-dir ../db_backup_csv --repeat 3
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from collections import defaultdict

import boto3

AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:620758375333:runtime/cdci_prd_analysis_agent-wCS0IP7dHa"
REGION = "ap-northeast-2"


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
        print(f"  [ERROR] {e}")
        return None


def check_rule(check_str: str, agent_output: dict) -> bool:
    import re
    step1 = agent_output.get("step1", {})
    step3 = agent_output.get("step3", {})

    count_match = re.search(r'required_nutrients\s+전체\s+개수가\s+(\d+)개\s+초과하면\s+위반', check_str)
    if count_match:
        limit = int(count_match.group(1))
        return len(step1.get("required_nutrients", [])) <= limit

    must_not = "must NOT" in check_str or "must not" in check_str
    tokens = re.findall(r"'([^']+)'", check_str)
    if not tokens:
        return True

    check_lower = check_str.lower()
    if "step1.required_nutrients" in check_lower:
        haystack = " ".join(n.get("name_ko", "") for n in step1.get("required_nutrients", []))
    elif "step3" in check_lower:
        haystack = " ".join(r.get("product_name", "") for r in step3.get("recommendations", []))
    elif "step1" in check_lower:
        haystack = json.dumps(step1, ensure_ascii=False)
    else:
        haystack = json.dumps(agent_output, ensure_ascii=False)

    found = any(kw in haystack for kw in tokens)
    return (not found) if must_not else found


def evaluate_case(case: dict, agent_output: dict) -> str:
    for rule in case["expected_output"].get("rules_violated_if_missing", []):
        if not check_rule(rule["check"], agent_output):
            return "FAIL"
    for rule in case["expected_output"].get("must_not_occur", []):
        if not check_rule(rule["check"], agent_output):
            return "FAIL"
    return "PASS"


def load_db_data(db_dir: str):
    import csv
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
    parser.add_argument("--repeat", type=int, default=3, help="케이스당 반복 횟수")
    parser.add_argument("--output", default="consistency_results.json")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    cases = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    products, unit_cache = load_db_data(args.db_dir)

    print(f"총 {len(cases)}개 케이스 × {args.repeat}회 = {len(cases)*args.repeat}번 호출\n{'='*60}")

    # case_id → [PASS/FAIL/ERROR, ...]
    results: dict[str, list[str]] = {}

    for case in cases:
        case_id = case["case_id"]
        results[case_id] = []
        payload = dict(case["input"])
        if not payload.get("products"):
            payload["products"] = products
        if not payload.get("unit_cache"):
            payload["unit_cache"] = unit_cache

        for i in range(1, args.repeat + 1):
            print(f"  [{case_id}] {i}/{args.repeat} ...", end=" ", flush=True)
            output = invoke_agent(payload)
            if output is None:
                status = "ERROR"
            else:
                status = evaluate_case(case, output)
            results[case_id].append(status)
            icon = "✅" if status == "PASS" else ("❌" if status == "FAIL" else "⚠️")
            print(icon)
            if i < args.repeat:
                time.sleep(args.delay)

    # 결과 분석
    print(f"\n{'='*60}")
    print(f"{'케이스':<12} {'결과':^20} {'일관성'}")
    print(f"{'-'*50}")

    unstable = []
    for case_id, runs in results.items():
        pass_count = runs.count("PASS")
        total = len(runs)
        icons = " ".join("✅" if r == "PASS" else ("❌" if r == "FAIL" else "⚠️") for r in runs)
        consistent = "🟢 안정" if len(set(runs)) == 1 else "🔴 불안정"
        print(f"{case_id:<12} {icons:<20} {pass_count}/{total} {consistent}")
        if len(set(runs)) > 1:
            unstable.append(case_id)

    total_runs = sum(len(v) for v in results.values())
    total_pass = sum(v.count("PASS") for v in results.values())
    stable_cases = sum(1 for v in results.values() if len(set(v)) == 1 and "PASS" in v)
    unstable_cases = len(unstable)

    print(f"\n{'='*60}")
    print(f"전체 실행: {total_runs}회 / PASS: {total_pass}회 ({total_pass/total_runs*100:.1f}%)")
    print(f"안정 케이스 (매번 PASS): {stable_cases}/{len(results)}")
    print(f"불안정 케이스: {unstable_cases}개 {unstable if unstable else ''}")

    # 저장
    output_data = {
        "summary": {
            "total_cases": len(cases),
            "repeat": args.repeat,
            "total_runs": total_runs,
            "pass_rate": f"{total_pass/total_runs*100:.1f}%",
            "stable_cases": stable_cases,
            "unstable_cases": unstable,
        },
        "details": results,
    }
    Path(args.output).write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장됨: {args.output}")


if __name__ == "__main__":
    main()
