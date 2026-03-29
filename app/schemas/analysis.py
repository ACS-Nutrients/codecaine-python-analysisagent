from decimal import Decimal
from pydantic import BaseModel, Field


# ── AgentCore /invocations 요청 ──────────────────────────────────

class AnalysisRequest(BaseModel):
    """App → AgentCore Runtime 호출 시 payload"""
    cognito_id: str
    intake_purpose: str = Field(..., description="섭취 목적")
    codef_health_data: dict | None = Field(None, description="CODEF 건강검진 JSON")
    medication_info: list[dict] | None = Field(None, description="의약품 투약 정보")
    current_supplements: list[dict] | None = Field(
        None,
        description="현재 복용 영양제 (App이 DB 조회 후 전달)"
    )
    unit_cache: dict | None = Field(
        None,
        description="단위 변환 테이블 (App이 unit_convertor 테이블 전체 조회 후 전달)"
    )
    products: list[dict] | None = Field(
        None,
        description="추천 후보 영양제 목록 (App이 products + product_nutrients 조회 후 전달)"
    )

    # TODO: chat_history 실제 형식은 챗봇 서비스 구현 후 확정 필요
    # 현재 가정: [{"role": "user"|"assistant", "content": "..."}]
    # 오케스트레이션 agent가 챗봇 재분석 호출 시 전달
    chat_history: list[dict] | None = Field(
        None,
        description="챗봇 대화 내역 — 형식 미확정, 챗봇 서비스 구현 후 수정 필요"
    )

    # 이전 분석 결과 맥락 (챗봇 재분석 시 참고용)
    # 오케스트레이션 agent가 "이전에 뭘 추천했는지" 알려주기 위해 전달
    # 형식: {"step1": {required_nutrients, summary}, "step2": {gaps}, "step3": {recommendations}}
    previous_analysis: dict | None = Field(
        None,
        description="이전 분석 결과 (step1/2/3) — 챗봇 재분석 시 맥락 유지용"
    )


# ── AgentCore /invocations 응답 ──────────────────────────────────

class RequiredNutrient(BaseModel):
    name_ko: str
    name_en: str | None = None
    rda_amount: Decimal
    unit: str
    reason: str = ""


class NutrientGapItem(BaseModel):
    nutrient_id: int | None = None
    name_ko: str
    name_en: str | None = None
    unit: str = "mg"
    current_amount: str
    gap_amount: str
    rda_amount: str


class RecommendationItem(BaseModel):
    rank: int
    product_id: int
    product_name: str
    product_brand: str
    recommend_serving: int
    serving_per_day: int | None = None
    covered_nutrients: list[str] = []


class LifestyleNotes(BaseModel):
    diet: str = ""
    exercise: str = ""
    sleep: str = ""
    supplement_timing: str = ""


class AnalysisSummary(BaseModel):
    overall_assessment: str = ""
    key_concerns: list[str] = []
    lifestyle_notes: LifestyleNotes = Field(default_factory=LifestyleNotes)
    risk_warnings: list[str] = []


class Step1Result(BaseModel):
    required_nutrients: list[RequiredNutrient]
    summary: AnalysisSummary


class Step2Result(BaseModel):
    gaps: list[NutrientGapItem]


class Step3Result(BaseModel):
    recommendations: list[RecommendationItem]


class AnalysisResponse(BaseModel):
    """
    AgentCore Runtime → App 최종 응답.
    App은 이 응답을 받아서 RDS에 저장.
    """
    cognito_id: str
    step1: Step1Result
    step2: Step2Result
    step3: Step3Result