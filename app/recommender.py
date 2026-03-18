"""
Step 3 — 추천 Agent

입력: 부족한 영양소 갭 목록
처리: 아이허브 크롤링 DB (products, product_nutrients) 활용
      1일 투약횟수(serving_per_day) 최소화로 복용 편의성 고려
출력: 추천 제품 목록 (커버리지 우선, 복용 횟수 최소화 순)
"""

import logging
from typing import Dict, List

from .db import cursor, get_conn

logger = logging.getLogger(__name__)

MAX_RECOMMENDATIONS = 5  # 최대 추천 제품 수


def recommend_supplements(gaps: List[Dict]) -> List[Dict]:
    """
    부족한 영양소를 채울 수 있는 제품 추천.

    알고리즘 (Greedy Set Cover):
    1. 각 부족 영양소를 포함하는 제품 후보 수집
    2. 커버 영양소 수 / serving_per_day 비율로 점수화
    3. 점수 높은 순으로 선택, 이미 채워진 영양소는 제외하며 반복
    4. MAX_RECOMMENDATIONS개 또는 모든 갭이 채워질 때까지 반복

    Args:
        gaps: nutrient_calculator.calculate_nutrient_gaps() 반환값

    Returns:
        [
            {
                "rank": int,
                "product_id": int,
                "product_name": str,
                "brand": str,
                "serving_per_day": int,
                "covered_nutrients": [{"name_ko": str, "amount_per_day": int, "unit": str}],
                "cover_count": int,   # 이 제품이 채우는 갭 영양소 수
            }
        ]
    """
    if not gaps:
        return []

    needed_ids = {g["nutrient_id"] for g in gaps}
    gap_info = {g["nutrient_id"]: g for g in gaps}

    conn = get_conn()
    try:
        with cursor(conn) as cur:
            # 부족 영양소를 포함하는 모든 제품 + 해당 영양소 함량 조회
            cur.execute(
                """
                SELECT
                    p.product_id,
                    p.product_brand,
                    p.product_name,
                    COALESCE(p.serving_per_day, 1) AS serving_per_day,
                    n.nutrient_id,
                    n.name_ko,
                    n.unit,
                    pn.amount_per_day
                FROM products p
                JOIN product_nutrients pn ON p.product_id = pn.product_id
                JOIN nutrients n ON pn.nutrient_id = n.nutrient_id
                WHERE n.nutrient_id = ANY(%s)
                ORDER BY p.product_id
                """,
                (list(needed_ids),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    # 제품별로 포함 영양소 묶기
    products: Dict[int, dict] = {}
    for row in rows:
        pid = row["product_id"]
        if pid not in products:
            products[pid] = {
                "product_id": pid,
                "product_name": row["product_name"],
                "brand": row["product_brand"],
                "serving_per_day": row["serving_per_day"],
                "nutrients": {},  # nutrient_id → {name_ko, amount_per_day, unit}
            }
        products[pid]["nutrients"][row["nutrient_id"]] = {
            "name_ko": row["name_ko"],
            "amount_per_day": row["amount_per_day"],
            "unit": row["unit"],
        }

    # Greedy Set Cover
    remaining_gaps = set(needed_ids)
    selected = []

    for _ in range(MAX_RECOMMENDATIONS):
        if not remaining_gaps:
            break

        best = _pick_best_product(products, remaining_gaps)
        if best is None:
            break

        covered = set(best["nutrients"].keys()) & remaining_gaps
        remaining_gaps -= covered

        selected.append(
            {
                "rank": len(selected) + 1,
                "product_id": best["product_id"],
                "product_name": best["product_name"],
                "brand": best["brand"],
                "serving_per_day": best["serving_per_day"],
                "covered_nutrients": [
                    {
                        "name_ko": v["name_ko"],
                        "amount_per_day": v["amount_per_day"],
                        "unit": v["unit"],
                    }
                    for v in best["nutrients"].values()
                ],
                "cover_count": len(covered),
            }
        )

        # 선택된 제품은 후보에서 제거
        del products[best["product_id"]]

    return selected


def _pick_best_product(
    products: Dict[int, dict],
    remaining_gaps: set,
) -> dict | None:
    """
    점수가 가장 높은 제품 반환.

    점수 = 커버하는 갭 영양소 수 / serving_per_day
    동점일 경우 serving_per_day가 낮은(복용 편한) 제품 우선
    """
    best_product = None
    best_score = -1.0
    best_serving = float("inf")

    for product in products.values():
        covered_count = len(set(product["nutrients"].keys()) & remaining_gaps)
        if covered_count == 0:
            continue

        serving = product["serving_per_day"] or 1
        score = covered_count / serving

        if score > best_score or (score == best_score and serving < best_serving):
            best_score = score
            best_serving = serving
            best_product = product

    return best_product
