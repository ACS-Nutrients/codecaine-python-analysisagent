"""
analysis_agent.py

개선 포인트
- 외부 input/output 스키마 변경 없음
- Step3에 약물/주의사항/required_nutrients를 함께 전달
- 제품 사전 필터링 추가
- 추천 결과 후처리 검증 추가
- LLM 추천이 비정상이면 규칙 기반 fallback ranking 수행
"""

import json
import logging
import math
import re
import time
from typing import Any

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
        tool_execution_counter.add(
            1,
            {"agent_name": AGENT_NAME, "tool_name": tool_name, "status": status},
        )
        tool_duration_histogram.record(
            time.time() - start,
            {"agent_name": AGENT_NAME, "tool_name": tool_name},
        )


SYSTEM_PROMPT_STEP1 = """당신은 영양 전문가 AI입니다.
주어진 건강 데이터를 분석하여 필요한 영양소와 권장량을 도출합니다.

분석 기준:
- 건강검진 수치 중 낮거나, 높거나, 경계값인 항목 파악
- 영양소-의약품 상호작용 검토
  (와파린+비타민K, 스타틴+CoQ10, 항생제→철분/칼슘/마그네슘 흡수 저하, 갑상선약+칼슘/철분 2시간 간격)
- 한국 영양소 기준 섭취량(KDRIs) 기반 권장량

근거 등급 규칙 (반드시 준수):
각 영양소의 reason 필드에 아래 세 가지 중 하나를 반드시 prefix로 명시할 것.
  [혈액검사 근거]: 혈액검사 수치가 결핍 기준을 명확히 초과한 경우
  [약물 상호작용]: 복용 중인 약물이 해당 영양소를 고갈·차단하는 경우 (근거 문헌 있음)
  [섭취 목적]: 섭취 목적·증상과 연관성이 있으나 혈액검사 수치 근거 없음

추천 개수 제한:
- [혈액검사 근거], [약물 상호작용] 영양소는 개수 제한 없음
- [섭취 목적] 영양소는 사용자가 명시한 섭취 목적의 개수만큼만 허용
  예) 섭취 목적이 "피로 개선" 1개 → [섭취 목적] 최대 1개
  예) 섭취 목적이 "피로 개선, 수면 개선, 면역 강화" 3개 → [섭취 목적] 최대 3개
- 건강검진 데이터가 없는 경우: [섭취 목적] 영양소 최대 3개로 제한

건강검진 데이터가 제공됐고 결핍 항목이 전혀 없는 경우 (특별 규칙):
- [섭취 목적] 영양소를 required_nutrients에 포함하지 않는다
- 대신 summary.lifestyle_notes에 섭취 목적에 맞는 식이·운동·수면 조언을 구체적으로 작성한다
- summary.overall_assessment에는 반드시 아래 흐름으로 작성한다:
  1) 사용자의 섭취 목적을 먼저 언급한다
  2) 건강검진 결과 해당 부분에 이상이 없음을 알린다
  3) 영양제보다는 생활습관 개선이 더 효과적일 수 있음을 안내한다
  4) lifestyle_notes의 구체적 조언으로 자연스럽게 연결한다

재분석 시 추가 기준:
- 섭취 목적(new_purpose)이 제공된 경우 해당 목적으로 분석
- 섭취 목적이 없는 경우 이전 분석 결과(previous_analysis)의 summary에서 기존 목적 파악
- 이전 분석 결과가 있는 경우 기존 추천과 달라진 이유를 reason 필드에 명시

required_nutrients 작성 규칙:
- 반드시 구체적인 rda_amount(숫자)와 unit(문자열)이 있는 영양소만 포함할 것
- 약물 상호작용으로 주의하거나 제한해야 하는 영양소(예: 와파린 복용 시 비타민K)는
  required_nutrients에 포함하지 말고 summary.key_concerns에 명시할 것
- rda_amount나 unit을 특정할 수 없는 경우 해당 영양소는 제외할 것
- 이미 현재 복용 영양제로 충분히 충족되고 있다고 판단되는 영양소는 required_nutrients에 넣지 말 것
- 정상 건강검진 수치만으로는 새로운 영양소를 추가하지 말 것

key_concerns 작성 규칙:
- 약물 관련 우려사항은 반드시 아래 형식으로 구체적으로 작성할 것
  형식: "[약물명] 복용 중 [영양소명] [주의 내용]"
- 약물명이 여러 개일 경우 각각 별도 항목으로 작성할 것
- 상호작용 정보를 모르는 경우 해당 항목 생략 (추측 금지)

risk_warnings 작성 규칙:
- 약물-영양소 상호작용 주의가 있으면 반드시 작성
- 없으면 빈 배열 허용
- 가능한 경우 "⚠️ " prefix 사용

summary 작성 지침:
- overall_assessment: 사용자의 건강 상태에 공감하는 따뜻한 어조로 작성하되, 검진 수치를 구체적으로 언급하며 현재 영양 상태를 7문장 이상으로 서술
- lifestyle_notes: 항목별로 실천 가능한 조언을 구체적으로 작성
- risk_warnings: 의약품 상호작용이나 과잉 섭취 위험이 있는 경우 경고 문구 추가 (없으면 빈 배열)

반드시 아래 JSON 형식으로만 응답하십시오. JSON 외 텍스트 없이 출력합니다.
{
  "required_nutrients": [
    {
      "name_ko": "비타민 D",
      "name_en": "Vitamin D",
      "rda_amount": 800,
      "unit": "IU",
      "reason": "[혈액검사 근거]: 검진 결과 비타민 D 수치가 낮음"
    }
  ],
  "summary": {
    "overall_assessment": "검진 수치를 포함한 7문장 이상의 전반적 영양 상태 평가",
    "key_concerns": [
      "아토르바스타틴(Atorvastatin) 복용 중 코엔자임 Q10 감소 가능 — 보충 권장"
    ],
    "lifestyle_notes": {
      "diet": "식이 조언",
      "exercise": "운동 조언",
      "sleep": "수면 조언",
      "supplement_timing": "복용 타이밍 안내"
    },
    "risk_warnings": ["⚠️ 와파린 복용 중 비타민K 함유 영양제 주의"]
  }
}"""


SYSTEM_PROMPT_STEP3 = """당신은 영양제 추천 전문가 AI입니다.
사용자의 영양소 갭 데이터와 제공된 영양제 목록을 바탕으로 최적의 영양제를 추천합니다.

매우 중요한 규칙:
- 제공된 영양제 목록에서만 추천 (임의 생성 금지)
- required_nutrients를 우선 충족하되, 불필요한 성분이 많은 제품은 피할 것
- 단일 성분 제품으로 해결 가능한 경우 종합비타민/복합제보다 단일제를 우선할 것
- risk_warnings, key_concerns, medication_info와 충돌 가능성이 있는 제품은 제외하거나 최하위로 보낼 것
- 어린이용/성별 불일치/명백히 대상이 맞지 않는 제품은 제외할 것
- required_nutrients와 전혀 관련 없는 제품은 추천하지 말 것
- 추천 개수는 "많을수록 좋음"이 아니라 "필요 최소 개수"가 원칙
- max_amount 초과 위험이 있으면 제외하거나 최소 serving으로 제한할 것
- covered_nutrients에는 실제로 해당 제품이 커버하는 필수 영양소만 작성할 것
- rank는 1부터 시작하는 연속 정수여야 함

권장 우선순위:
1) 필수 영양소 커버
2) 불필요 성분 최소화
3) 복용 편의성
4) 과량 위험 최소화

반드시 아래 JSON 형식으로만 응답하십시오.
{
  "recommendations": [
    {
      "rank": 1,
      "product_id": 12,
      "product_name": "제품명",
      "product_brand": "브랜드",
      "recommend_serving": 1,
      "serving_per_day": 1,
      "covered_nutrients": ["비타민 D"]
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
        kb_context = execute_step(
            "kb_retrieval",
            retrieve_drug_interactions,
            req.medication_info or [],
            [],
        )
        step1_user_prompt = self._build_step1_prompt(req)

        if kb_context:
            step1_user_prompt += f"\n\n[의약품-영양소 상호작용 참고 정보]\n{kb_context}"
            logger.info(f"[{req.cognito_id}] KB 컨텍스트 주입됨")
            kb_context_counter.add(1, {"agent_name": AGENT_NAME, "status": "hit"})
        else:
            kb_context_counter.add(1, {"agent_name": AGENT_NAME, "status": "miss"})

        step1_raw = execute_step(
            "step1_llm",
            self._call_llm,
            system=SYSTEM_PROMPT_STEP1,
            user=step1_user_prompt,
        )
        step1_data = self._parse_json(step1_raw)

        required_nutrients = self._sanitize_required_nutrients(
            step1_data.get("required_nutrients", [])
        )
        summary = step1_data.get("summary", {})
        summary = summary if isinstance(summary, dict) else {}

        logger.info(f"[{req.cognito_id}] Step 1 완료 — 영양소 {len(required_nutrients)}개")

        # ── Step 2: Lambda 갭 계산 ───────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 2 시작")
        gaps = execute_step(
            "nutrient_calc",
            self._call_lambda,
            cognito_id=req.cognito_id,
            required_nutrients=required_nutrients,
            current_supplements=req.current_supplements or [],
            unit_cache=req.unit_cache or {},
        )
        logger.info(f"[{req.cognito_id}] Step 2 완료 — 갭 {len(gaps)}개")

        # Step3용 안전 필터링
        active_gaps = self._active_gaps(gaps)
        filtered_products = self._filter_products(
            req=req,
            required_nutrients=required_nutrients,
            gaps=active_gaps,
            products=req.products or [],
            summary=summary,
        )

        # ── Step 3: LLM 추천 ─────────────────────────────────────
        logger.info(f"[{req.cognito_id}] Step 3 시작")

        recommendations: list[dict] = []
        if filtered_products and active_gaps:
            step3_raw = execute_step(
                "step3_llm",
                self._call_llm,
                system=SYSTEM_PROMPT_STEP3,
                user=self._build_step3_prompt(
                    req=req,
                    gaps=active_gaps,
                    products=filtered_products,
                    required_nutrients=required_nutrients,
                    summary=summary,
                ),
            )
            step3_data = self._parse_json(step3_raw)
            recommendations = step3_data.get("recommendations", []) or []

        recommendations = self._validate_and_finalize_recommendations(
            req=req,
            recommendations=recommendations,
            required_nutrients=required_nutrients,
            gaps=active_gaps,
            products=filtered_products,
            summary=summary,
        )

        if not recommendations and filtered_products and active_gaps:
            logger.warning(f"[{req.cognito_id}] LLM 추천 결과 부적절/비어있음 → 규칙 기반 fallback 사용")
            recommendations = self._rule_based_rank_products(
                req=req,
                required_nutrients=required_nutrients,
                gaps=active_gaps,
                products=filtered_products,
                summary=summary,
            )

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

    # ── LLM 호출 ────────────────────────────────────────────────

    def _call_llm(self, system: str, user: str) -> str:
        if settings.LLM_PROVIDER == "openai":
            return self._call_openai(system, user)
        if settings.LLM_PROVIDER == "anthropic":
            return self._call_anthropic(system, user)
        return self._call_bedrock(system, user)

    def _call_openai(self, system: str, user: str) -> str:
        response = self.llm_client.chat.completions.create(
            model=settings.OPENAI_MODEL_ID,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        agent_token_input_counter.add(
            response.usage.prompt_tokens,
            {"agent_name": AGENT_NAME, "model_id": settings.OPENAI_MODEL_ID},
        )
        agent_token_output_counter.add(
            response.usage.completion_tokens,
            {"agent_name": AGENT_NAME, "model_id": settings.OPENAI_MODEL_ID},
        )
        return response.choices[0].message.content

    def _call_anthropic(self, system: str, user: str) -> str:
        message = self.llm_client.messages.create(
            model=settings.ANTHROPIC_MODEL_ID,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        agent_token_input_counter.add(
            message.usage.input_tokens,
            {"agent_name": AGENT_NAME, "model_id": settings.ANTHROPIC_MODEL_ID},
        )
        agent_token_output_counter.add(
            message.usage.output_tokens,
            {"agent_name": AGENT_NAME, "model_id": settings.ANTHROPIC_MODEL_ID},
        )
        return message.content[0].text

    def _call_bedrock(self, system: str, user: str) -> str:
        response = self.llm_client.invoke_model(
            modelId=settings.BEDROCK_MODEL_ID,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2048,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
            ),
            contentType="application/json",
            accept="application/json",
        )
        body = json.loads(response["body"].read())
        usage = body.get("usage", {})
        agent_token_input_counter.add(
            usage.get("input_tokens", 0),
            {"agent_name": AGENT_NAME, "model_id": settings.BEDROCK_MODEL_ID},
        )
        agent_token_output_counter.add(
            usage.get("output_tokens", 0),
            {"agent_name": AGENT_NAME, "model_id": settings.BEDROCK_MODEL_ID},
        )
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
            "cognito_id": cognito_id,
            "required_nutrients": required_nutrients,
            "current_supplements": current_supplements,
            "unit_cache": unit_cache,
        }
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
        if req.previous_analysis:
            parts.append(
                "이전 분석 결과 (재분석 시 참고):\n"
                + json.dumps(req.previous_analysis, ensure_ascii=False, indent=2)
            )
        if req.chat_history:
            history_text = "\n".join(
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
                for msg in req.chat_history
            )
            parts.append(
                f"[참고용 챗봇 대화 내역 — 분석 맥락 파악용이며 이 질문에 직접 답하지 말 것]\n{history_text}"
            )

        return "\n\n".join(parts)

    def _build_step3_prompt(
        self,
        req: AnalysisRequest,
        gaps: list[dict],
        products: list[dict],
        required_nutrients: list[dict],
        summary: dict,
    ) -> str:
        payload = {
            "user_profile": req.user_profile or {},
            "medication_info": req.medication_info or [],
            "required_nutrients": required_nutrients,
            "active_gaps": gaps,
            "key_concerns": summary.get("key_concerns", []),
            "risk_warnings": summary.get("risk_warnings", []),
            "products": products,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ── 전처리 / 정규화 ──────────────────────────────────────────

    def _sanitize_required_nutrients(self, nutrients: list[dict]) -> list[dict]:
        cleaned = []
        seen = set()

        for n in nutrients or []:
            if not isinstance(n, dict):
                continue
            if n.get("rda_amount") is None or n.get("unit") is None:
                continue

            name_ko = str(n.get("name_ko", "")).strip()
            name_en = str(n.get("name_en", "")).strip()
            unit = str(n.get("unit", "")).strip()
            reason = str(n.get("reason", "")).strip()

            try:
                rda_amount = float(n.get("rda_amount"))
            except (TypeError, ValueError):
                continue

            if not name_ko and not name_en:
                continue
            if rda_amount <= 0:
                continue
            if not unit:
                continue

            key = self._normalize_name(name_ko or name_en)
            if key in seen:
                continue
            seen.add(key)

            cleaned.append(
                {
                    "name_ko": name_ko,
                    "name_en": name_en,
                    "rda_amount": rda_amount,
                    "unit": unit,
                    "reason": reason,
                }
            )

        return cleaned

    def _active_gaps(self, gaps: list[dict]) -> list[dict]:
        result = []
        for g in gaps or []:
            if not isinstance(g, dict):
                continue
            gap_amount = self._safe_float(g.get("gap_amount", 0))
            if gap_amount > 0:
                result.append(g)
        return result

    # ── 제품 필터링 ──────────────────────────────────────────────

    def _filter_products(
        self,
        req: AnalysisRequest,
        required_nutrients: list[dict],
        gaps: list[dict],
        products: list[dict],
        summary: dict,
    ) -> list[dict]:
        if not products:
            return []

        required_aliases = self._build_required_alias_map(required_nutrients, gaps)
        blocked_keywords = self._infer_blocked_keywords(summary)
        gender = self._extract_gender(req.user_profile or {})

        filtered = []

        for product in products:
            if not isinstance(product, dict):
                continue

            coverage = self._extract_product_coverage(product, required_aliases)
            if not coverage:
                # 필수 영양소 하나도 못 덮으면 제외
                continue

            name = self._product_text(product).lower()

            # 어린이용 제외
            if any(k in name for k in ["kids", "children", "child", "gummy vites", "릴 크리터스"]):
                continue

            # 성별 불일치 제외
            if gender == "male" and any(k in name for k in ["여성", "여자", "women", "woman", "female"]):
                continue
            if gender == "female" and any(k in name for k in ["남성", "남자", "men", "man", "male"]):
                continue

            # 요양 목적과 무관하게 명백히 다른 카테고리 제품 제외
            if any(k in name for k in ["collagen", "콜라겐", "glucosamine", "글루코사민", "probiotic", "프로바이오틱"]):
                if len(coverage) == 0:
                    continue

            # key_concerns / risk_warnings 에서 추론된 차단 키워드 제외
            if blocked_keywords and any(k in name for k in blocked_keywords):
                continue

            p = dict(product)
            p["_matched_required"] = sorted(coverage)
            filtered.append(p)

        return filtered

    def _infer_blocked_keywords(self, summary: dict) -> set[str]:
        text_parts = []
        if isinstance(summary, dict):
            text_parts.extend(summary.get("key_concerns", []) or [])
            text_parts.extend(summary.get("risk_warnings", []) or [])

        text = " ".join(str(x) for x in text_parts).lower()
        blocked = set()

        # 너무 공격적으로 막지는 않고, 명확한 케이스만 간단히 처리
        if "와파린" in text or "warfarin" in text:
            blocked.add("vitamin k")
            blocked.add("비타민k")
        if "자몽" in text or "grapefruit" in text:
            blocked.add("grapefruit")
            blocked.add("자몽")
        return blocked

    # ── 추천 검증 / 보정 ─────────────────────────────────────────

    def _validate_and_finalize_recommendations(
        self,
        req: AnalysisRequest,
        recommendations: list[dict],
        required_nutrients: list[dict],
        gaps: list[dict],
        products: list[dict],
        summary: dict,
    ) -> list[dict]:
        if not recommendations:
            return []

        required_aliases = self._build_required_alias_map(required_nutrients, gaps)
        product_map = {self._safe_int(p.get("product_id")): p for p in products}
        finalized = []

        for rec in recommendations:
            if not isinstance(rec, dict):
                continue

            product_id = self._safe_int(rec.get("product_id"))
            if not product_id or product_id not in product_map:
                continue

            product = product_map[product_id]
            matched = self._extract_product_coverage(product, required_aliases)
            if not matched:
                continue

            recommend_serving = self._safe_int(rec.get("recommend_serving")) or 1
            serving_per_day = self._safe_int(rec.get("serving_per_day")) or self._safe_int(product.get("serving_per_day")) or 1

            safe_serving = self._compute_safe_serving(
                product=product,
                requested_serving=recommend_serving,
                gaps=gaps,
                summary=summary,
            )
            recommend_serving = max(1, safe_serving)

            finalized.append(
                {
                    "rank": 0,  # 아래에서 재정렬
                    "product_id": product_id,
                    "product_name": rec.get("product_name") or product.get("product_name") or "",
                    "product_brand": rec.get("product_brand") or product.get("product_brand") or "",
                    "recommend_serving": recommend_serving,
                    "serving_per_day": serving_per_day,
                    "covered_nutrients": sorted(matched),
                }
            )

        # 중복 제거
        dedup = {}
        for item in finalized:
            pid = item["product_id"]
            prev = dedup.get(pid)
            if not prev or len(item["covered_nutrients"]) > len(prev["covered_nutrients"]):
                dedup[pid] = item

        items = list(dedup.values())

        # 필수 영양소 커버, 불필요 성분 적은 순으로 재정렬
        items.sort(
            key=lambda x: self._post_validation_sort_key(
                x=x,
                product_map=product_map,
                required_aliases=required_aliases,
            )
        )

        # 최소 개수 원칙: 최대 3개
        items = items[:3]

        for idx, item in enumerate(items, start=1):
            item["rank"] = idx

        return items

    def _post_validation_sort_key(
        self,
        x: dict,
        product_map: dict[int, dict],
        required_aliases: dict[str, set[str]],
    ):
        product = product_map.get(x["product_id"], {})
        coverage_count = len(x.get("covered_nutrients", []))
        irrelevant_count = self._count_irrelevant_nutrients(product, required_aliases)
        serving_per_day = self._safe_int(x.get("serving_per_day")) or 99
        recommend_serving = self._safe_int(x.get("recommend_serving")) or 99

        # coverage 많을수록 좋고, irrelevant / 복용량 적을수록 좋음
        return (-coverage_count, irrelevant_count, serving_per_day, recommend_serving, x["product_id"])

    def _compute_safe_serving(
        self,
        product: dict,
        requested_serving: int,
        gaps: list[dict],
        summary: dict,
    ) -> int:
        # 지금 데이터 구조상 제품의 nutrient별 함량 필드가 항상 보장되지 않을 수 있어서
        # 최소한 과도한 serving 추천만 방지
        serving_per_day = self._safe_int(product.get("serving_per_day")) or 1
        requested_serving = max(1, requested_serving)

        # 과도한 숫자 방지
        upper_bound = max(1, serving_per_day)
        requested_serving = min(requested_serving, upper_bound)

        # risk_warnings에서 명시적 마그네슘 주의가 있으면 1로 제한
        risk_text = " ".join(summary.get("risk_warnings", []) or []).lower() if isinstance(summary, dict) else ""
        product_text = self._product_text(product).lower()

        if ("마그네슘" in product_text or "magnesium" in product_text) and ("마그네슘" in risk_text or "magnesium" in risk_text):
            return 1

        return requested_serving

    # ── 규칙 기반 fallback ranking ───────────────────────────────

    def _rule_based_rank_products(
        self,
        req: AnalysisRequest,
        required_nutrients: list[dict],
        gaps: list[dict],
        products: list[dict],
        summary: dict,
    ) -> list[dict]:
        required_aliases = self._build_required_alias_map(required_nutrients, gaps)

        scored = []
        for product in products:
            matched = self._extract_product_coverage(product, required_aliases)
            if not matched:
                continue

            coverage_count = len(matched)
            irrelevant_count = self._count_irrelevant_nutrients(product, required_aliases)
            is_multivitamin = self._is_multivitamin(product)
            serving_per_day = self._safe_int(product.get("serving_per_day")) or 1
            recommend_serving = self._compute_safe_serving(
                product=product,
                requested_serving=1,
                gaps=gaps,
                summary=summary,
            )

            score = 0
            score += coverage_count * 100
            score -= irrelevant_count * 5
            score -= max(serving_per_day - 1, 0) * 3
            if is_multivitamin:
                score -= 15

            scored.append(
                {
                    "score": score,
                    "item": {
                        "rank": 0,
                        "product_id": self._safe_int(product.get("product_id")) or 0,
                        "product_name": product.get("product_name", ""),
                        "product_brand": product.get("product_brand", ""),
                        "recommend_serving": recommend_serving,
                        "serving_per_day": serving_per_day,
                        "covered_nutrients": sorted(matched),
                    },
                }
            )

        scored.sort(
            key=lambda x: (
                -x["score"],
                self._is_multivitamin_by_item(x["item"]),
                x["item"]["serving_per_day"],
                x["item"]["product_id"],
            )
        )

        final_items = [x["item"] for x in scored[:3]]
        for idx, item in enumerate(final_items, start=1):
            item["rank"] = idx
        return final_items

    # ── Helper ──────────────────────────────────────────────────

    def _build_required_alias_map(
        self,
        required_nutrients: list[dict],
        gaps: list[dict],
    ) -> dict[str, set[str]]:
        alias_map: dict[str, set[str]] = {}

        def add_alias(base_name: str, alias: str):
            base_key = self._normalize_name(base_name)
            alias_key = self._normalize_name(alias)
            if not base_key or not alias_key:
                return
            alias_map.setdefault(base_key, set()).add(alias_key)

        for n in required_nutrients or []:
            name_ko = str(n.get("name_ko", "")).strip()
            name_en = str(n.get("name_en", "")).strip()

            canonical = name_ko or name_en
            if not canonical:
                continue

            add_alias(canonical, canonical)
            if name_ko:
                add_alias(canonical, name_ko)
            if name_en:
                add_alias(canonical, name_en)

            for extra in self._default_aliases_for_name(canonical):
                add_alias(canonical, extra)

        # gaps에만 있고 required_nutrients 문자열이 좀 다른 경우도 흡수
        for g in gaps or []:
            name_ko = str(g.get("name_ko", "")).strip()
            name_en = str(g.get("name_en", "")).strip()
            canonical = name_ko or name_en
            if not canonical:
                continue
            add_alias(canonical, canonical)
            if name_ko:
                add_alias(canonical, name_ko)
            if name_en:
                add_alias(canonical, name_en)
            for extra in self._default_aliases_for_name(canonical):
                add_alias(canonical, extra)

        return alias_map

    def _default_aliases_for_name(self, name: str) -> list[str]:
        norm = self._normalize_name(name)
        aliases = [name]

        mapping = {
            "vitamind": ["비타민d", "vitamin d", "vitamin d3", "비타민d3", "cholecalciferol", "콜레칼시페롤"],
            "coenzymeq10": ["코엔자임q10", "코큐텐", "coq10", "ubiquinone", "유비퀴논"],
            "omega3": ["오메가3", "오메가-3", "omega-3", "dha", "epa", "총오메가3"],
            "magnesium": ["마그네슘", "magnesium"],
            "vitaminb12": ["비타민b12", "vitamin b12", "b12", "코발라민"],
            "vitaminbcomplex": ["비타민b복합체", "비타민b 컴플렉스", "vitamin b complex", "b-complex", "b complex"],
            "chromium": ["크롬", "크로뮴", "chromium", "chromium picolinate"],
            "potassium": ["칼륨", "potassium"],
        }

        for key, vals in mapping.items():
            if key in norm:
                aliases.extend(vals)

        return aliases

    def _extract_product_coverage(
        self,
        product: dict,
        required_aliases: dict[str, set[str]],
    ) -> set[str]:
        product_names = {
            self._normalize_name(x)
            for x in self._extract_product_nutrient_names(product)
            if self._normalize_name(x)
        }

        matched = set()
        for canonical, aliases in required_aliases.items():
            if product_names.intersection(aliases):
                matched.add(self._display_name_from_canonical(canonical, required_aliases))
        return matched

    def _display_name_from_canonical(self, canonical: str, required_aliases: dict[str, set[str]]) -> str:
        # canonical이 이미 사람이 읽을 수 있는 이름이 아닐 수 있으므로 alias 중 가장 짧고 보기 좋은 것 선택
        aliases = list(required_aliases.get(canonical, []))
        if not aliases:
            return canonical
        # 한글 우선
        aliases.sort(key=lambda x: (not self._contains_korean(x), len(x)))
        return aliases[0]

    def _extract_product_nutrient_names(self, product: dict) -> list[str]:
        values = []

        for key in ["covered_nutrients", "nutrients", "ingredient_names", "main_nutrients"]:
            raw = product.get(key)
            if isinstance(raw, list):
                values.extend([str(x) for x in raw if x is not None])

        # 문자열 필드에서도 조금 더 주워오기
        for key in ["product_name", "name"]:
            raw = product.get(key)
            if raw:
                values.append(str(raw))

        return values

    def _count_irrelevant_nutrients(
        self,
        product: dict,
        required_aliases: dict[str, set[str]],
    ) -> int:
        product_names = {
            self._normalize_name(x)
            for x in self._extract_product_nutrient_names(product)
            if self._normalize_name(x)
        }

        all_required_aliases = set()
        for aliases in required_aliases.values():
            all_required_aliases.update(aliases)

        irrelevant = [x for x in product_names if x and x not in all_required_aliases]
        return len(irrelevant)

    def _is_multivitamin(self, product: dict) -> bool:
        text = self._product_text(product).lower()
        if any(k in text for k in ["종합", "멀티", "multivitamin", "multi vitamin", "complete", "elite", "2/day"]):
            return True

        covered = product.get("covered_nutrients", [])
        if isinstance(covered, list) and len(covered) >= 6:
            return True

        return False

    def _is_multivitamin_by_item(self, item: dict) -> bool:
        text = (item.get("product_name") or "").lower()
        return any(k in text for k in ["종합", "멀티", "multivitamin", "complete", "elite", "2/day"])

    def _extract_gender(self, user_profile: dict) -> str:
        raw = str(
            user_profile.get("gender")
            or user_profile.get("sex")
            or user_profile.get("gender_code")
            or ""
        ).strip().lower()

        if raw in {"m", "male", "man", "남", "남성", "남자"}:
            return "male"
        if raw in {"f", "female", "woman", "여", "여성", "여자"}:
            return "female"
        return ""

    def _product_text(self, product: dict) -> str:
        return " ".join(
            [
                str(product.get("product_name", "")),
                str(product.get("product_brand", "")),
                " ".join([str(x) for x in product.get("covered_nutrients", [])]) if isinstance(product.get("covered_nutrients"), list) else "",
            ]
        )

    def _normalize_name(self, value: str) -> str:
        v = str(value or "").strip().lower()
        v = v.replace("(", " ").replace(")", " ")
        v = v.replace("/", " ").replace(",", " ").replace("·", " ")
        v = v.replace("-", "")
        v = re.sub(r"\s+", "", v)
        return v

    def _contains_korean(self, text: str) -> bool:
        return bool(re.search(r"[가-힣]", str(text)))

    def _safe_float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _safe_int(self, value: Any) -> int:
        try:
            if value is None:
                return 0
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()

        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}\n원문: {text[:500]}")
            raise ValueError(f"LLM 응답 파싱 실패: {e}") from e