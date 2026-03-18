"""
Lambda: action_nutrient_calc

Bedrock AgentCore 컨테이너에서 직접 invoke하는 Lambda.
DB 접근 없음 — VPC 설정 불필요.

입력 (analysis_agent.py에서 직접 호출):
{
  "cognito_id": "...",
  "required_nutrients":  [{ name_ko, name_en, rda_amount, unit, reason }],
  "current_supplements": [{ product_name, serving_per_day, ingredients: [{name, amount}] }],
  "unit_cache":          { "IU": "0.000025", "µg": "0.001" }
}

출력:
{
  "gaps": [{ nutrient_id, name_ko, name_en, unit, current_amount, gap_amount, rda_amount }]
}
"""

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MG_UNITS = {"mg", "MG"}


def to_mg(amount: Decimal, unit: str | None, unit_cache: dict) -> Decimal:
    if not unit or unit in MG_UNITS:
        return amount
    factor = unit_cache.get(unit)
    if factor is None:
        logger.warning(f"단위 변환 정보 없음: '{unit}' — 변환 없이 사용")
        return amount
    return amount * factor


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
        current_mg  = to_mg(raw_current, req_unit, unit_cache)
        rda_mg      = to_mg(req_rda, req_unit, unit_cache)

        gap_mg = max(Decimal("0"), rda_mg - current_mg)
        gap_mg = gap_mg.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        gaps.append({
            "nutrient_id":    req.get("nutrient_id"),   # App DB에서 매핑
            "name_ko":        name_ko,
            "name_en":        req.get("name_en"),
            "unit":           "mg",
            "current_amount": str(current_mg.quantize(Decimal("0.0001"))),
            "gap_amount":     str(gap_mg),
            "rda_amount":     str(rda_mg.quantize(Decimal("0.0001"))),
        })

        logger.info(f"[갭] {name_ko}: 현재={current_mg}mg | RDA={rda_mg}mg | 갭={gap_mg}mg")

    return {"gaps": gaps}