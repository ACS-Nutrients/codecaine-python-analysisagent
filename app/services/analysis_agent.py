"""
analysis_agent.py

LLM_PROVIDER 환경변수로 LLM 제공자 전환 가능.
  - LLM_PROVIDER=bedrock    → AWS Bedrock 사용 (운영)
  - LLM_PROVIDER=anthropic  → Anthropic API 직접 사용
  - LLM_PROVIDER=openai     → OpenAI API 사용 (테스트)

products 데이터는 App이 DB 조회 후 payload로 전달.
DB 연결 정보가 변경되어도 코드 수정 없이 App의 .env만 변경하면 됨.
"""

import json
import logging

import boto3

from app.core.config import settings
from app.schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    NutrientGapItem,
    RecommendationItem,
    RequiredNutrient,
    Step1Result,
    Step2Result,
    Step3Result,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_STEP1 = """당신은 영양 전문가 AI입니다.
주어진 건강 데이터를 분석하여 필요한 영양소와 권장량을 도출합니다.

분석 기준:
- 건강검진 수치 중 낮거나 경계값인 항목 파악
- 영양소-의약품 상호작용 검토
  (와파린+비타민K, 스타틴+CoQ10, 항생제→철분/칼슘/마그네슘 흡수 저하, 갑상선약+칼슘/철분 2시간 간격)
- 섭취 목적에 맞는 영양소 우선 제안
- 한국 영양소 기준 섭취량(KDRIs) 기반 권장량

반드시 아래 JSON 형식으로만 응답하십시오. JSON 외 텍스트 없이 출력합니다.
{
  "required_nutrients": [
    {
      "name_ko": "비타민 D",
      "name_en": "Vitamin D",
      "rda_amount": 800,
      "unit": "IU",
      "reason": "검진 결과 결핍 위험"
    }
  ],
  "summary": {
    "overall_assessment": "전반적인 영양 상태 평가",
    "key_concerns": ["우려사항1", "우려사항2"],
    "lifestyle_notes": "생활습관 메모"
  }
}"""

SYSTEM_PROMPT_STEP3 = """당신은 영양제 추천 전문가 AI입니다.
사용자의 영양소 갭 데이터와 제공된 영양제 목록을 바탕으로 최적의 영양제를 추천합니다.

추천 기준:
- 제공된 영양제 목록에서만 추천 (임의로 만들어내지 말 것)
- 부족한 영양소 커버율 높은 제품 우선
- serving_per_day 낮을수록 우선 (복용 편의성)
- max_amount 초과 위험 제품 하위 순위
- 최대 5개 추천

반드시 아래 JSON 형식으로만 응답하십시오.
{
  "recommendations": [
    {
      "rank": 1,
      "product_id": 12,
      "product_name": "제품명",
      "product_brand": "브랜드",
      "recommend_serving": 2,
      "serving_per_day": 2,
      "covered_nutrients": ["비타민 D", "마그네슘"]
    }
  ]
}"""


class AnalysisAgent:
    def __init__(self):
        session = boto3.Session(region_name=settings.AWS_REGION)
        self.lambda_client = session.client("lambda")

        if settings.LLM_PROVIDER == "openai":
            from openai import OpenAI
            self.llm_client = OpenAI(api_key=settings.OPENAI_API_KEY)
            logger.info("LLM Provider: OpenAI")
        elif settings.LLM_PROVIDER == "anthropic":
            import anthropic
            self.llm_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("LLM Provider: Anthropic API")
        else:
            self.llm_client = session.client("bedrock-runtime")
            logger.info("LLM Provider: AWS Bedrock")

    async def run(self, req: AnalysisRequest) -> AnalysisResponse:
        # ── Step 1: LLM 분석 ─────────────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 1 시작")
        step1_raw = self._call_llm(
            system=SYSTEM_PROMPT_STEP1,
            user=self._build_step1_prompt(req),
        )
        step1_data         = self._parse_json(step1_raw)
        required_nutrients = step1_data.get("required_nutrients", [])
        summary            = step1_data.get("summary", {})
        logger.info(f"[{req.cognito_id}] Step 1 완료 — 영양소 {len(required_nutrients)}개")

        # ── Step 2: Lambda 갭 계산 ───────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 2 시작")
        gaps = self._call_lambda(
            cognito_id=req.cognito_id,
            required_nutrients=required_nutrients,
            current_supplements=req.current_supplements or [],
            unit_cache=req.unit_cache or {},
        )
        logger.info(f"[{req.cognito_id}] Step 2 완료 — 갭 {len(gaps)}개")

        # ── Step 3: LLM 추천 ─────────────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 3 시작")
        step3_raw = self._call_llm(
            system=SYSTEM_PROMPT_STEP3,
            user=self._build_step3_prompt(gaps, req.products or []),
        )
        step3_data      = self._parse_json(step3_raw)
        recommendations = step3_data.get("recommendations", [])
        logger.info(f"[{req.cognito_id}] Step 3 완료 — 추천 {len(recommendations)}개")

        return AnalysisResponse(
            cognito_id=req.cognito_id,
            step1=Step1Result(
                required_nutrients=[RequiredNutrient(**n) for n in required_nutrients],
                summary=summary,
            ),
            step2=Step2Result(
                gaps=[NutrientGapItem(**g) for g in gaps],
            ),
            step3=Step3Result(
                recommendations=[RecommendationItem(**r) for r in recommendations],
            ),
        )

    # ── LLM 호출 (provider 분기) ──────────────────────────────────

    def _call_llm(self, system: str, user: str) -> str:
        if settings.LLM_PROVIDER == "openai":
            return self._call_openai(system, user)
        elif settings.LLM_PROVIDER == "anthropic":
            return self._call_anthropic(system, user)
        return self._call_bedrock(system, user)

    def _call_openai(self, system: str, user: str) -> str:
        response = self.llm_client.chat.completions.create(
            model=settings.OPENAI_MODEL_ID,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _call_anthropic(self, system: str, user: str) -> str:
        message = self.llm_client.messages.create(
            model=settings.ANTHROPIC_MODEL_ID,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    def _call_bedrock(self, system: str, user: str) -> str:
        response = self.llm_client.invoke_model(
            modelId=settings.BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2048,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        body = json.loads(response["body"].read())
        return body["content"][0]["text"]

    # ── Lambda 호출 ───────────────────────────────────────────────

    def _call_lambda(
        self,
        cognito_id: str,
        required_nutrients: list[dict],
        current_supplements: list[dict],
        unit_cache: dict,
    ) -> list[dict]:
        payload = {
            "cognito_id":          cognito_id,
            "required_nutrients":  required_nutrients,
            "current_supplements": current_supplements,
            "unit_cache":          unit_cache,
        }
        # analysis_agent.py _call_lambda() 수정
        response = self.lambda_client.invoke(
        FunctionName=settings.LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),  # 수정
)
        result = json.loads(response["Payload"].read())
        return result.get("gaps", [])

    # ── 프롬프트 빌더 ─────────────────────────────────────────────

    def _build_step1_prompt(self, req: AnalysisRequest) -> str:
        parts = [
            f"사용자 ID: {req.cognito_id}",
            f"섭취 목적: {req.intake_purpose}",
        ]
        if req.codef_health_data:
            parts.append(
                "건강검진 데이터:\n"
                + json.dumps(req.codef_health_data, ensure_ascii=False, indent=2)
            )
        if req.medication_info:
            parts.append(
                "복용 의약품:\n"
                + json.dumps(req.medication_info, ensure_ascii=False, indent=2)
            )
        if req.current_supplements:
            parts.append(
                "현재 복용 영양제:\n"
                + json.dumps(req.current_supplements, ensure_ascii=False, indent=2)
            )
        return "\n\n".join(parts)

    def _build_step3_prompt(self, gaps: list[dict], products: list[dict]) -> str:
        """
        gaps + products 데이터를 LLM에 전달.
        products는 App이 DB에서 조회해서 payload로 넘긴 데이터.
        DB가 바뀌어도 이 코드는 수정 불필요 — App의 .env만 변경하면 됨.
        """
        active_gaps = [g for g in gaps if float(g.get("gap_amount", 0)) > 0]

        parts = [
            "아래 영양소 갭을 채울 수 있는 최적의 영양제를 추천하세요.",
            "\n영양소 갭 목록:\n"
            + json.dumps(active_gaps, ensure_ascii=False, indent=2),
        ]

        if products:
            parts.append(
                "\n추천 가능한 영양제 목록 (이 목록에서만 추천하세요):\n"
                + json.dumps(products, ensure_ascii=False, indent=2)
            )
        else:
            parts.append("\n※ 영양제 목록이 제공되지 않았습니다. 추천을 건너뜁니다.")

        return "\n".join(parts)

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}\n원문: {text[:300]}")
            raise ValueError(f"LLM 응답 파싱 실패: {e}") from e