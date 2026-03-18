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
        description="현재 복용 영양제 (DB 복제본에서 App이 조회 후 전달)"
    )
    unit_cache: dict | None = Field(
        None,
        description="단위 변환 테이블 (App이 DB에서 조회 후 전달)"
    )


# ── AgentCore /invocations 응답 ──────────────────────────────────
# App이 이 응답을 받아서 각 step 결과를 별도 저장 API로 DB에 저장

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


class Step1Result(BaseModel):
    required_nutrients: list[RequiredNutrient]
    summary: dict


class Step2Result(BaseModel):
    gaps: list[NutrientGapItem]


class Step3Result(BaseModel):
    recommendations: list[RecommendationItem]


class AnalysisResponse(BaseModel):
    """
    AgentCore Runtime → App 최종 응답.
    App은 이 응답을 받아서 별도 저장 API를 호출하여 RDS에 저장.
    """
    cognito_id: str
    step1: Step1Result
    step2: Step2Result
    step3: Step3Result