"""
Step 1 — LLM Agent (1차 판단)

입력: CODEF 건강검진 데이터 + 복용 영양제 정보 + 의약품 투약정보 + 섭취 목적
참조: 영양제-의약품 상호작용 지식 (프롬프트 내장, 추후 Bedrock KB로 교체 가능)
출력: {nutrient_id: recommended_amount}
"""

import json
import logging
import os
from datetime import date
from typing import Dict, List

import boto3

from .db import cursor, get_conn

logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
)
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")

# 영양제-의약품 주요 상호작용 지식베이스 (내장)
# 추후 AWS Bedrock Knowledge Base로 교체 가능
DRUG_INTERACTION_KB = """
[영양제-의약품 주요 상호작용]
- 와파린(혈액희석제): 비타민K 과다 섭취 금지, 오메가3·비타민E 고용량 주의 (출혈 위험)
- 스타틴(콜레스테롤약): 코엔자임Q10 고갈 유발 → CoQ10 보충 권장
- 메트포르민(당뇨약): 비타민B12 흡수 저하 → B12 보충 권장
- 갑상선약(레보티록신): 칼슘·마그네슘·철분 흡수 방해 → 복용 간격 4시간 이상
- ACE억제제(고혈압약): 칼륨 축적 위험 → 칼륨 보충 주의
- 이뇨제: 마그네슘·칼륨·아연 손실 → 보충 권장
- 항생제(퀴놀론계·테트라사이클린): 칼슘·마그네슘·아연과 킬레이트 → 복용 간격 2시간 이상
- SSRI(항우울제): 세인트존스워트 병용 금지 (세로토닌 증후군)
- 아스피린: 비타민C·철분 흡수에 영향, 비타민K 주의
"""


def analyze_health_data(
    user_id: str,
    purpose: str,
    medications: List[str],
    health_data: Dict,
) -> Dict:
    """
    사용자 건강 데이터를 분석하여 필요한 영양소 및 권장량 반환.

    Args:
        user_id:     Cognito 사용자 ID
        purpose:     영양제 복용 목적 (예: "피로 개선", "면역력 강화")
        medications: 복용 중인 의약품 목록
        health_data: CODEF 건강검진 결과 + 사용자 입력값
                     {exam_date, gender, age, height, weight, exam_items: [...]}

    Returns:
        {
            "summary": str,           # 건강 상태 요약
            "llm_recommended": {nutrient_id: amount}  # 필요 영양소 권장량
        }
    """
    conn = get_conn()
    try:
        with cursor(conn) as cur:
            # 사용자 기본 정보 (알레르기, 만성질환, 증상)
            cur.execute(
                """
                SELECT ans_birth_dt, ans_gender, ans_height, ans_weight,
                       ans_allergies, ans_chron_diseases, ans_current_conditions
                FROM analysis_userdata
                WHERE cognito_id = %s
                """,
                (user_id,),
            )
            user = cur.fetchone()

            # 현재 복용 영양제 + 성분
            cur.execute(
                """
                SELECT
                    s.ans_product_name,
                    s.ans_serving_per_day,
                    json_agg(
                        json_build_object(
                            'ingredient', i.ans_ingredient_name,
                            'amount_per_serving', i.ans_nutrient_amount
                        )
                    ) FILTER (WHERE i.ans_ingredient_id IS NOT NULL) AS ingredients
                FROM analysis_supplements s
                LEFT JOIN anaysis_current_ingredients i
                       ON s.ans_current_id = i.ans_current_id
                WHERE s.cognito_id = %s AND s.ans_is_active = true
                GROUP BY s.ans_current_id, s.ans_product_name, s.ans_serving_per_day
                """,
                (user_id,),
            )
            supplements = cur.fetchall() or []

            # 영양소 카탈로그 (LLM이 선택할 수 있는 목록)
            cur.execute(
                "SELECT nutrient_id, name_ko, name_en, unit FROM nutrients ORDER BY nutrient_id"
            )
            nutrients = cur.fetchall()

            # 현재 일일 섭취량 계산 (영양소명 기준)
            cur.execute(
                """
                SELECT n.name_ko,
                       SUM(i.ans_nutrient_amount * COALESCE(s.ans_serving_per_day, 1)) AS daily_total
                FROM analysis_supplements s
                JOIN anaysis_current_ingredients i ON s.ans_current_id = i.ans_current_id
                JOIN nutrients n
                     ON n.name_ko = i.ans_ingredient_name
                     OR n.name_en = i.ans_ingredient_name
                WHERE s.cognito_id = %s AND s.ans_is_active = true
                GROUP BY n.name_ko
                """,
                (user_id,),
            )
            current_intake = {
                row["name_ko"]: int(row["daily_total"] or 0)
                for row in cur.fetchall()
            }
    finally:
        conn.close()

    if not nutrients:
        raise ValueError("nutrients 테이블이 비어 있습니다")

    # DB에 사용자 없으면 health_data의 값 사용
    age = health_data.get("age")
    gender = health_data.get("gender", 0)
    if user:
        if user.get("ans_birth_dt"):
            today = date.today()
            b = user["ans_birth_dt"]
            age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        gender = user.get("ans_gender", gender)

    prompt = _build_prompt(
        user=user,
        age=age,
        gender=gender,
        health_data=health_data,
        supplements=supplements,
        current_intake=current_intake,
        medications=medications,
        purpose=purpose,
        nutrients=nutrients,
    )

    llm_result = _call_bedrock(prompt)

    # name_ko → nutrient_id 역매핑
    name_to_id = {n["name_ko"]: n["nutrient_id"] for n in nutrients}
    id_set = {n["nutrient_id"] for n in nutrients}

    llm_recommended = {}
    for item in llm_result.get("recommendations", []):
        nid = item.get("nutrient_id")
        if not nid:
            nid = name_to_id.get(item.get("name_ko", ""))
        amount = item.get("amount", 0)
        if nid and nid in id_set and isinstance(amount, (int, float)) and amount > 0:
            llm_recommended[nid] = int(amount)

    return {
        "summary": llm_result.get("summary", ""),
        "llm_recommended": llm_recommended,
    }


def _build_prompt(
    user, age, gender, health_data, supplements, current_intake,
    medications, purpose, nutrients,
) -> str:
    gender_str = "여성" if gender == 1 else "남성"
    allergies = (user or {}).get("ans_allergies") or "없음"
    chronic = (user or {}).get("ans_chron_diseases") or "없음"
    conditions = (user or {}).get("ans_current_conditions") or "없음"

    # CODEF 건강검진 항목
    exam_items = health_data.get("exam_items", [])
    exam_str = (
        json.dumps(exam_items, ensure_ascii=False, indent=2)
        if exam_items
        else "검진 데이터 없음"
    )

    supp_str = (
        json.dumps([dict(s) for s in supplements], ensure_ascii=False, indent=2)
        if supplements
        else "없음"
    )
    intake_str = json.dumps(current_intake, ensure_ascii=False)
    med_str = "、".join(medications) if medications else "없음"
    nutrient_catalog = json.dumps(
        [
            {
                "nutrient_id": n["nutrient_id"],
                "name_ko": n["name_ko"],
                "name_en": n["name_en"],
                "unit": n["unit"],
            }
            for n in nutrients
        ],
        ensure_ascii=False,
        indent=2,
    )

    return f"""당신은 전문 임상 영양사입니다. 아래 정보를 종합 분석하여 이 사람에게 필요한 영양소와 권장 섭취량을 JSON으로 반환하세요.

## 사용자 기본 정보
- 성별: {gender_str}
- 나이: {age}세
- 신장: {health_data.get("height") or (user or {}).get("ans_height")}cm
- 체중: {health_data.get("weight") or (user or {}).get("ans_weight")}kg
- 알레르기: {allergies}
- 만성질환: {chronic}
- 현재 증상/관심: {conditions}

## 복용 목적
{purpose}

## CODEF 건강검진 결과
{exam_str}

## 현재 복용 중인 영양제
{supp_str}

## 현재 일일 영양소 섭취량 (원래 단위 기준)
{intake_str}

## 복용 중인 의약품 (상호작용 반드시 고려)
{med_str}

## 영양제-의약품 상호작용 참조
{DRUG_INTERACTION_KB}

## 선택 가능한 영양소 카탈로그 (반드시 아래 목록의 nutrient_id만 사용)
{nutrient_catalog}

## 지시사항
1. 건강검진 결과의 부족/과잉 항목을 우선 고려하세요.
2. 의약품과의 상호작용을 확인하여 위험한 영양소는 제외하거나 양을 줄이세요.
3. 이미 충분히 섭취 중인 영양소는 제외하세요.
4. 한국인 영양섭취기준(KDRIs) 범위 내에서 권장량을 설정하세요.
5. 추천 개수는 3~7개로 제한하세요.
6. summary 필드에 이 사람의 건강 상태 요약 및 주요 보충이 필요한 이유를 1~2문장으로 작성하세요.

## 응답 형식 (JSON만 반환, 다른 텍스트 없음)
{{
  "summary": "<건강 상태 요약>",
  "recommendations": [
    {{
      "nutrient_id": <int>,
      "name_ko": "<string>",
      "amount": <int>,
      "unit": "<string>",
      "reason": "<string>"
    }}
  ]
}}"""


def _call_bedrock(prompt: str) -> dict:
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.1},
    )
    content = response["output"]["message"]["content"][0]["text"].strip()

    # 마크다운 코드블록 제거
    if "```" in content:
        parts = content.split("```")
        content = parts[1] if len(parts) > 1 else parts[0]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()

    return json.loads(content)
