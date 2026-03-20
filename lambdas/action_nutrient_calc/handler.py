"""
Lambda: action_nutrient_calc

단위 변환 로직:
  mg  → 그대로
  mcg → unit_convertor에서 'mcg' 키로 factor 조회 (0.001)
  IU  → unit_convertor에서 영양소 이름(name_ko)으로 factor 조회
         예: 비타민D → 0.000025, 비타민A → 0.000030
"""

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MG_UNITS = {"mg", "MG"}

# handler.py의 to_mg() 함수 수정
def to_mg(amount, unit, unit_cache, nutrient_name_ko=""):
    if not unit or unit in MG_UNITS:
        return amount

    if unit.lower() in ('mcg', 'µg', 'μg'):
        factor = unit_cache.get('mcg')
        if factor is None:
            factor = Decimal('0.001')
        return amount * factor

    if unit == 'IU':
        # 띄어쓰기 제거 후 조회 ("비타민 D" → "비타민D")
        name_normalized = nutrient_name_ko.replace(" ", "")
        factor = (
            unit_cache.get(nutrient_name_ko) or
            unit_cache.get(name_normalized)
        )
        if factor is None:
            logger.warning(f"unit_convertor에 '{nutrient_name_ko}' IU 변환 factor 없음")
            return amount
        return amount * factor

    logger.warning(f"알 수 없는 단위: '{unit}'")
    return amount


def build_intake_map(current_supplements: list[dict]) -> dict[str, Decimal]:
    """현재 복용 영양제에서 영양소명 기준 일일 총 섭취량 집계"""
    intake_map: dict[str, Decimal] = {}
    for supp in current_supplements:
        spd = int(supp.get("serving_per_day") or 1)
        for ing in supp.get("ingredients", []):
            name   = (ing.get("name") or "").strip()
            amount = ing.get("amount")
            if not name or amount is None:
                continue
            daily = Decimal(str(amount)) * spd
            intake_map[name] = intake_map.get(name, Decimal("0")) + daily
    return intake_map


def lambda_handler(event: dict, context) -> dict:
    """
    입력:
      cognito_id, required_nutrients, current_supplements, unit_cache

    unit_cache 형식:
      {
        "mcg":   "0.001",
        "비타민D": "0.000025",
        "비타민A": "0.000030",
        "비타민E": "0.00067",
        ...
      }
      → App이 unit_convertor 테이블 전체를 조회해서 전달
    """
    logger.info(f"수신: {json.dumps(event)[:300]}")

    cognito_id          = event["cognito_id"]
    required_nutrients  = event["required_nutrients"]
    current_supplements = event.get("current_supplements", [])
    unit_cache_raw      = event.get("unit_cache", {})
    unit_cache          = {k: Decimal(str(v)) for k, v in unit_cache_raw.items()}

    intake_map = build_intake_map(current_supplements)
    logger.info(f"[{cognito_id}] 섭취 영양소 {len(intake_map)}종 집계")

    gaps = []
    for req in required_nutrients:
        name_ko  = req["name_ko"]
        req_unit = req["unit"]
        req_rda  = Decimal(str(req["rda_amount"]))

        raw_current = intake_map.get(name_ko, Decimal("0"))

        # IU 변환 시 영양소 이름으로 factor 조회
        current_mg = to_mg(raw_current, req_unit, unit_cache, name_ko)
        rda_mg     = to_mg(req_rda,     req_unit, unit_cache, name_ko)

        gap_mg = max(Decimal("0"), rda_mg - current_mg)
        gap_mg = gap_mg.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        gaps.append({
            "nutrient_id":    req.get("nutrient_id"),
            "name_ko":        name_ko,
            "name_en":        req.get("name_en"),
            "unit":           "mg",
            "current_amount": str(current_mg.quantize(Decimal("0.0001"))),
            "gap_amount":     str(gap_mg),
            "rda_amount":     str(rda_mg.quantize(Decimal("0.0001"))),
        })

        logger.info(
            f"[갭] {name_ko} (단위:{req_unit}): "
            f"현재={current_mg}mg | RDA={rda_mg}mg | 갭={gap_mg}mg"
        )

    return {"gaps": gaps}