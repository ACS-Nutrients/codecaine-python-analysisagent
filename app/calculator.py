# 단위 변환 계수 (예시)
UNIT_CONVERTER = {
    'IU_to_mg_VitD': 0.025,
    'mcg_to_mg': 0.001
}

def calculate_gap(nutrient, target, current):
    """
    영양소 갭 계산: Target - Current = Gap
    """
    # TODO: 여기서 단위 변환(IU -> mg 등) 로직을 수행하세요.
    gap = float(target) - float(current)
    return {
        "nutrient": nutrient,
        "gap_amount": max(0, gap),
        "unit": "mg"
    }