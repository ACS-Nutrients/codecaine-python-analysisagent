import json
import logging

from .analyzer import analyze_health_data
from .nutrient_calculator import calculate_nutrient_gaps
from .recommender import recommend_supplements

logger = logging.getLogger(__name__)


def handler(event, context):
    """
    AWS Bedrock Agent Action Group 핸들러

    apiPath: /full-analysis
    parameters:
      - user_id:     Cognito 사용자 ID (필수)
      - purpose:     영양제 복용 목적 (선택, 기본값: "건강 유지")
      - medications: 복용 중인 의약품 JSON 배열 문자열 (선택)
      - health_data: CODEF 건강검진 + 사용자 입력값 JSON 문자열 (선택)
    """
    api_path = event.get("apiPath")
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}

    try:
        if api_path == "/full-analysis":
            result = _run_full_analysis(params)
        else:
            result = {"error": f"지원하지 않는 API Path: {api_path}"}

    except Exception as e:
        logger.exception("분석 처리 중 오류 발생")
        result = {"error": str(e)}

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event["actionGroup"],
            "apiPath": api_path,
            "httpMethod": event["httpMethod"],
            "httpStatusCode": 200,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(result, ensure_ascii=False)
                }
            },
        },
    }


def _run_full_analysis(params: dict) -> dict:
    user_id = params.get("user_id")
    if not user_id:
        raise ValueError("user_id 파라미터가 필요합니다")

    purpose = params.get("purpose", "건강 유지")
    medications = json.loads(params.get("medications", "[]"))
    health_data = json.loads(params.get("health_data", "{}"))

    # ── Step 1. LLM Agent (1차 판단) ──────────────────────────────────────────
    # CODEF 건강검진 데이터 + 복용 영양제 + 의약품 상호작용 분석
    # 출력: {nutrient_id: recommended_amount}
    llm_result = analyze_health_data(
        user_id=user_id,
        purpose=purpose,
        medications=medications,
        health_data=health_data,
    )
    llm_recommended = llm_result["llm_recommended"]  # {nutrient_id: amount}

    # ── Step 2. 영양소 갭 계산 ────────────────────────────────────────────────
    # LLM 권장량 - 현재 섭취량, 최대 섭취량(nutrient_reference_intake) 초과 방지
    # 단위 변환: IU → mg, µg → mg (unit_convertor 테이블)
    gaps = calculate_nutrient_gaps(
        user_id=user_id,
        llm_recommended=llm_recommended,
    )

    # ── Step 3. 추천 Agent ────────────────────────────────────────────────────
    # 부족 영양소를 채울 수 있는 제품 추천 (복용 횟수 최소화, 커버리지 최대화)
    recommendations = recommend_supplements(gaps)

    return {
        "analysis_summary": llm_result["summary"],
        "llm_recommended": llm_recommended,
        "nutrient_gaps": gaps,
        "recommendations": recommendations,
    }
