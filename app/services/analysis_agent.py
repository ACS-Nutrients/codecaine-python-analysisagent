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
import time

import boto3

from app.core.config import settings
from app.metrics import (
    tool_execution_counter,
    tool_duration_histogram,
    agent_token_input_counter,
    agent_token_output_counter,
    kb_context_counter,
)
from app.services.kb_retriever import retrieve_drug_interactions
from app.schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    AnalysisSummary,
    NutrientGapItem,
    RecommendationItem,
    RequiredNutrient,
    Step1Result,
    Step2Result,
    Step3Result,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "analysis-agent"


def execute_step(tool_name: str, tool_fn, *args, **kwargs):
    start = time.time()
    status = "success"
    try:
        return tool_fn(*args, **kwargs)
    except Exception as e:
        status = "error"
        raise e
    finally:
        tool_execution_counter.add(1, {"agent_name": AGENT_NAME, "tool_name": tool_name, "status": status})
        tool_duration_histogram.record(time.time() - start, {"agent_name": AGENT_NAME, "tool_name": tool_name})

SYSTEM_PROMPT_STEP1 = """당신은 영양 전문가 AI입니다.
주어진 건강 데이터를 분석하여 필요한 영양소와 권장량을 도출합니다.

분석 기준:
- 건강검진 수치 중 낮거나 경계값인 항목 파악
- 영양소-의약품 상호작용 검토
  (와파린+비타민K, 스타틴+CoQ10, 항생제→철분/칼슘/마그네슘 흡수 저하, 갑상선약+칼슘/철분 2시간 간격)
- 섭취 목적에 맞는 영양소 우선 제안
- 한국 영양소 기준 섭취량(KDRIs) 기반 권장량

재분석 시 추가 기준:
- 섭취 목적(new_purpose)이 제공된 경우 해당 목적으로 분석
- 섭취 목적이 없는 경우 이전 분석 결과(previous_analysis)의 summary에서 기존 목적 파악
- 이전 분석 결과가 있는 경우 기존 추천과 달라진 이유를 reason 필드에 명시

required_nutrients 작성 규칙:
- 반드시 구체적인 rda_amount(숫자)와 unit(문자열)이 있는 영양소만 포함할 것
- 약물 상호작용으로 주의하거나 제한해야 하는 영양소(예: 와파린 복용 시 비타민K)는
  required_nutrients에 포함하지 말고 summary.key_concerns에 명시할 것
- rda_amount나 unit을 특정할 수 없는 경우 해당 영양소는 제외할 것

key_concerns 작성 규칙:
- 약물 관련 우려사항은 반드시 아래 형식으로 구체적으로 작성할 것
  형식: "[약물명] 복용 중 [영양소명] [주의 내용]"
  예시: "몬테루칸정(Montelukast) 복용 중 비타민 D 흡수 저하 가능 — 보충 권장"
        "와파린(Warfarin) 복용 중 비타민 K 과다 섭취 주의 — 항응고 효과 감소 위험"
- "알레르기 약물과 영양소 상호작용 주의"처럼 뭉뚱그린 표현은 금지
- 약물명이 여러 개일 경우 각각 별도 항목으로 작성할 것
- 상호작용 정보를 모르는 경우 해당 항목 생략 (추측 금지)

summary 작성 지침:
- overall_assessment: 검진 수치를 구체적으로 언급하며 현재 영양 상태를 3문장 이상으로 서술.
  예) "비타민D 수치가 18.0 ng/mL로 정상 범위(30~100)의 60% 수준입니다. 철분 관련 페리틴도 20 ng/mL로 경계값에 해당하며..."
- lifestyle_notes: 항목별로 실천 가능한 조언을 구체적으로 작성
  - diet: 식품 예시 포함 (예: "등 푸른 생선 주 2회, 달걀노른자 매일 섭취 권장")
  - exercise: 운동 종류·빈도 포함 (예: "야외 유산소 운동 주 3회, 회당 30분 이상으로 비타민D 합성 촉진")
  - sleep: 수면과 영양소 연관성 포함 (예: "마그네슘 섭취 시 취침 1시간 전 복용 시 수면 질 개선에 도움")
  - supplement_timing: 영양소별 최적 복용 시간 안내 (예: "지용성 비타민(A·D·E·K)은 식후, 철분은 공복 복용")
- risk_warnings: 의약품 상호작용이나 과잉 섭취 위험이 있는 경우 경고 문구 추가 (없으면 빈 배열)

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
    "overall_assessment": "검진 수치를 포함한 3문장 이상의 전반적 영양 상태 평가",
    "key_concerns": [
      "혈색소 수치 경계값 — 철분 결핍 가능성 모니터링 필요",
      "몬테루칸정(Montelukast) 복용 중 비타민 D 흡수 저하 가능 — 보충 권장"
    ],
    "lifestyle_notes": {
      "diet": "식이 조언 (식품 예시 포함)",
      "exercise": "운동 조언 (종류·빈도 포함)",
      "sleep": "수면 조언 (영양소 연관성 포함)",
      "supplement_timing": "영양제 복용 타이밍 안내"
    },
    "risk_warnings": ["⚠️ 와파린 복용 중 비타민K 함유 영양제 주의"]
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
        kb_context = execute_step("kb_retrieval", retrieve_drug_interactions, req.medication_info or [], [])
        step1_user_prompt = self._build_step1_prompt(req)
        if kb_context:
            step1_user_prompt += f"\n\n[의약품-영양소 상호작용 참고 정보]\n{kb_context}"
            logger.info(f"[{req.cognito_id}] KB 컨텍스트 주입됨")
            kb_context_counter.add(1, {"agent_name": AGENT_NAME, "status": "hit"})
        else:
            kb_context_counter.add(1, {"agent_name": AGENT_NAME, "status": "miss"})
        step1_raw = execute_step("step1_llm", self._call_llm,
            system=SYSTEM_PROMPT_STEP1,
            user=step1_user_prompt,
        )
        step1_data         = self._parse_json(step1_raw)
        required_nutrients = [
            n for n in step1_data.get("required_nutrients", [])
            if n.get("rda_amount") is not None and n.get("unit") is not None
        ]
        summary            = step1_data.get("summary", {})
        logger.info(f"[{req.cognito_id}] Step 1 완료 — 영양소 {len(required_nutrients)}개")

        # ── Step 2: Lambda 갭 계산 ───────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 2 시작")
        gaps = execute_step("nutrient_calc", self._call_lambda,
            cognito_id=req.cognito_id,
            required_nutrients=required_nutrients,
            current_supplements=req.current_supplements or [],
            unit_cache=req.unit_cache or {},
        )
        logger.info(f"[{req.cognito_id}] Step 2 완료 — 갭 {len(gaps)}개")

        # ── Step 3: LLM 추천 ─────────────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 3 시작")
        step3_raw = execute_step("step3_llm", self._call_llm,
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
                summary=AnalysisSummary(**summary) if isinstance(summary, dict) else summary,
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
        agent_token_input_counter.add(response.usage.prompt_tokens, {"agent_name": AGENT_NAME, "model_id": settings.OPENAI_MODEL_ID})
        agent_token_output_counter.add(response.usage.completion_tokens, {"agent_name": AGENT_NAME, "model_id": settings.OPENAI_MODEL_ID})
        return response.choices[0].message.content

    def _call_anthropic(self, system: str, user: str) -> str:
        message = self.llm_client.messages.create(
            model=settings.ANTHROPIC_MODEL_ID,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        agent_token_input_counter.add(message.usage.input_tokens, {"agent_name": AGENT_NAME, "model_id": settings.ANTHROPIC_MODEL_ID})
        agent_token_output_counter.add(message.usage.output_tokens, {"agent_name": AGENT_NAME, "model_id": settings.ANTHROPIC_MODEL_ID})
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
        usage = body.get("usage", {})
        agent_token_input_counter.add(usage.get("input_tokens", 0), {"agent_name": AGENT_NAME, "model_id": settings.BEDROCK_MODEL_ID})
        agent_token_output_counter.add(usage.get("output_tokens", 0), {"agent_name": AGENT_NAME, "model_id": settings.BEDROCK_MODEL_ID})
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
            Payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        result = json.loads(response["Payload"].read())
        return result.get("gaps", [])

    # ── 프롬프트 빌더 ─────────────────────────────────────────────

    def _build_step1_prompt(self, req: AnalysisRequest) -> str:
        purpose = req.new_purpose or req.intake_purpose or ""
        parts = [
            f"사용자 ID: {req.cognito_id}",
            f"섭취 목적: {purpose}",
        ]
        if req.user_profile:
            parts.append(
                "사용자 프로필:\n"
                + json.dumps(req.user_profile, ensure_ascii=False, indent=2)
            )
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

        # 챗봇 재분석 시 이전 분석 맥락 포함
        if req.previous_analysis:
            parts.append(
                "이전 분석 결과 (재분석 시 참고):\n"
                + json.dumps(req.previous_analysis, ensure_ascii=False, indent=2)
            )

        # TODO: chat_history 형식 확정 후 파싱 방식 수정 필요
        # 현재는 {"role": "user"|"assistant", "content": "..."} 형식으로 가정
        if req.chat_history:
            history_text = "\n".join(
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
                for msg in req.chat_history
            )
            parts.append(f"챗봇 대화 내역:\n{history_text}")

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
        # JSON 블록 앞뒤 텍스트 제거
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}\n원문: {text[:300]}")
            raise ValueError(f"LLM 응답 파싱 실패: {e}") from e