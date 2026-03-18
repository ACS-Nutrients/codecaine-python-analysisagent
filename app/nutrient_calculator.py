"""
Step 2 — 영양소 갭 계산

입력: user_id, {nutrient_id: llm_recommended_amount}
처리: 최대 섭취량(nutrient_reference_intake) vs 현재 섭취량 비교
      단위 변환: IU → mg, µg → mg (unit_convertor 테이블 참조)
출력: [{nutrient_id, name_ko, unit, current_amount, recommended_amount, max_amount, gap_amount}]
"""

import logging
from datetime import date
from typing import Dict, List

from .db import cursor, get_conn

logger = logging.getLogger(__name__)


def calculate_nutrient_gaps(
    user_id: str,
    llm_recommended: Dict[int, int],
) -> List[Dict]:
    """
    LLM 권장량과 현재 섭취량을 비교하여 부족한 영양소와 갭 반환.

    단위 변환 후 모두 mg 기준으로 통일하여 비교.
    gap_amount는 max_amount를 초과하지 않도록 캡핑.

    Returns:
        [
            {
                "nutrient_id": int,
                "name_ko": str,
                "name_en": str,
                "unit": str,          # 원래 단위 (표시용)
                "current_amount": int,
                "recommended_amount": int,
                "max_amount": int | None,
                "gap_amount": int,    # 추가로 섭취해야 할 양 (원래 단위 기준)
            }
        ]
    """
    if not llm_recommended:
        return []

    conn = get_conn()
    try:
        with cursor(conn) as cur:
            # 사용자 정보 (나이, 성별 — reference intake 조회용)
            cur.execute(
                "SELECT ans_birth_dt, ans_gender FROM analysis_userdata WHERE cognito_id = %s",
                (user_id,),
            )
            user = cur.fetchone()

            # IU 변환 계수 테이블 (vitamin_name → convert_unit)
            cur.execute("SELECT vitamin_name, convert_unit FROM unit_convertor")
            convertor = {row["vitamin_name"]: float(row["convert_unit"]) for row in cur.fetchall()}

            # 현재 복용 영양소 계산 — 영양제별 성분 × 복용 횟수 합산
            cur.execute(
                """
                SELECT n.nutrient_id, n.name_ko, n.unit,
                       SUM(i.ans_nutrient_amount * COALESCE(s.ans_serving_per_day, 1)) AS daily_total
                FROM analysis_supplements s
                JOIN anaysis_current_ingredients i ON s.ans_current_id = i.ans_current_id
                JOIN nutrients n
                     ON n.name_ko = i.ans_ingredient_name
                     OR n.name_en = i.ans_ingredient_name
                WHERE s.cognito_id = %s AND s.ans_is_active = true
                GROUP BY n.nutrient_id, n.name_ko, n.unit
                """,
                (user_id,),
            )
            current_rows = cur.fetchall()
            # {nutrient_id: (amount_in_original_unit, unit)}
            current_by_id = {
                row["nutrient_id"]: {
                    "amount": float(row["daily_total"] or 0),
                    "unit": row["unit"],
                }
                for row in current_rows
            }

            # 영양소 기본 정보 (nutrient_id 기준)
            nutrient_ids = list(llm_recommended.keys())
            cur.execute(
                "SELECT nutrient_id, name_ko, name_en, unit FROM nutrients WHERE nutrient_id = ANY(%s)",
                (nutrient_ids,),
            )
            nutrients = {row["nutrient_id"]: dict(row) for row in cur.fetchall()}

            # 사용자 나이·성별 계산
            age, gender = _resolve_age_gender(user)

            # nutrient_reference_intake 조회 (나이·성별 범위)
            ref_intakes = {}
            for nid in nutrient_ids:
                if age is not None and gender is not None:
                    cur.execute(
                        """
                        SELECT rda_amount, max_amount
                        FROM nutrient_reference_intake
                        WHERE nutrient_id = %s
                          AND gender = %s
                          AND age_min <= %s AND age_max >= %s
                        LIMIT 1
                        """,
                        (nid, gender, age, age),
                    )
                else:
                    cur.execute(
                        """
                        SELECT rda_amount, max_amount
                        FROM nutrient_reference_intake
                        WHERE nutrient_id = %s
                        ORDER BY age_min
                        LIMIT 1
                        """,
                        (nid,),
                    )
                ref_intakes[nid] = cur.fetchone()
    finally:
        conn.close()

    gaps = []
    for nid, recommended_raw in llm_recommended.items():
        nutrient = nutrients.get(nid)
        if not nutrient:
            continue

        unit = nutrient["unit"]
        ref = ref_intakes.get(nid)

        # 현재 섭취량 (mg 환산)
        current_info = current_by_id.get(nid, {"amount": 0.0, "unit": unit})
        current_mg = _to_mg(current_info["amount"], current_info["unit"], nutrient["name_ko"], convertor)

        # LLM 권장량 (mg 환산)
        recommended_mg = _to_mg(float(recommended_raw), unit, nutrient["name_ko"], convertor)

        # 최대 섭취량 (mg 환산)
        max_mg = None
        if ref and ref.get("max_amount"):
            max_mg = _to_mg(float(ref["max_amount"]), unit, nutrient["name_ko"], convertor)

        # 갭 계산: LLM 권장량 - 현재 섭취량
        gap_mg = recommended_mg - current_mg
        if gap_mg <= 0:
            continue  # 이미 충분히 섭취 중

        # 최대치 초과 방지
        if max_mg is not None:
            gap_mg = min(gap_mg, max_mg - current_mg)
        if gap_mg <= 0:
            continue

        # 결과는 원래 단위 기준으로 역변환 (표시용)
        gap_original = _from_mg(gap_mg, unit, nutrient["name_ko"], convertor)
        current_original = _from_mg(current_mg, unit, nutrient["name_ko"], convertor)
        max_original = _from_mg(max_mg, unit, nutrient["name_ko"], convertor) if max_mg is not None else None

        gaps.append(
            {
                "nutrient_id": nid,
                "name_ko": nutrient["name_ko"],
                "name_en": nutrient["name_en"],
                "unit": unit,
                "current_amount": round(current_original),
                "recommended_amount": recommended_raw,
                "max_amount": round(max_original) if max_original is not None else None,
                "gap_amount": round(gap_original),
            }
        )

    return gaps


# ─────────────────────────────────────────────
# 단위 변환 헬퍼
# ─────────────────────────────────────────────

def _to_mg(amount: float, unit: str, name_ko: str, convertor: dict) -> float:
    """영양소 양을 mg 기준으로 변환"""
    if unit == "mg":
        return amount
    if unit in ("µg", "mcg", "μg"):
        return amount * 0.001
    if unit == "IU":
        factor = convertor.get(name_ko)
        if factor:
            return amount * factor
        logger.warning(f"IU 변환 계수 없음: {name_ko}, 변환 없이 반환")
    return amount


def _from_mg(amount_mg: float, unit: str, name_ko: str, convertor: dict) -> float:
    """mg 값을 원래 단위로 역변환 (표시용)"""
    if unit == "mg":
        return amount_mg
    if unit in ("µg", "mcg", "μg"):
        return amount_mg / 0.001
    if unit == "IU":
        factor = convertor.get(name_ko)
        if factor and factor != 0:
            return amount_mg / factor
    return amount_mg


def _resolve_age_gender(user) -> tuple:
    """사용자 나이·성별 반환 (없으면 None, None)"""
    if not user:
        return None, None
    age = None
    if user.get("ans_birth_dt"):
        today = date.today()
        b = user["ans_birth_dt"]
        age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    gender = user.get("ans_gender")
    return age, gender
