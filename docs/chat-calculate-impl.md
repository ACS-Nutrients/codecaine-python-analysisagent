# chat-calculate 구현 명세

> 챗봇 서비스에서 재분석을 요청하는 전용 API 구현

---

## 1. Analysis Agent (`analysis-agent-ver1`)

### 1-1. `app/schemas/analysis.py` - AnalysisRequest 수정

`new_purpose` 필드 추가, 기존 `intake_purpose`는 일반 분석용으로 유지.

```python
class AnalysisRequest(BaseModel):
    cognito_id: str
    intake_purpose: str | None = None         # 일반 분석용 (기존)
    new_purpose: str | None = None            # 챗봇 재분석용 - 없으면 previous_analysis에서 맥락 파악
    chat_history: list[dict] | None = None    # 이미 있음
    previous_analysis: dict | None = None     # 재분석 맥락 — { "step1_summary": "..." } 형태로 전달
    # 나머지 기존 필드 유지
```

---

### 1-2. `app/services/analysis_agent.py` - Step1 프롬프트 수정

`_build_step1_prompt`에서 재분석 컨텍스트 주입.

```python
def _build_step1_prompt(self, req: AnalysisRequest) -> str:
    # new_purpose 있으면 새 목적, 없으면 intake_purpose 사용
    purpose = req.new_purpose or req.intake_purpose or ""
    parts = [
        f"사용자 ID: {req.cognito_id}",
        f"섭취 목적: {purpose}",
    ]

    # 재분석인 경우 이전 분석 결과 전체를 맥락으로 주입
    if req.previous_analysis:
        parts.append(
            "이전 분석 결과 (재분석 맥락):\n"
            + json.dumps(req.previous_analysis, ensure_ascii=False, indent=2)
        )

    # 나머지 기존 필드 (codef_health_data, medication_info 등) 유지
    ...
```

---

### 1-3. SYSTEM_PROMPT_STEP1 수정

재분석 시 LLM이 이전 결과를 맥락으로 활용하도록 지시 추가.

```
- new_purpose가 있으면 해당 목적으로 분석 (Super Agent가 채팅에서 파싱해서 전달)
- new_purpose가 없으면 previous_analysis.step1_summary 텍스트에서 기존 섭취 목적 파악
- 이전 분석 결과가 있는 경우 기존 추천과의 변화 이유를 reason 필드에 명시
```

---

## 2. Analysis Backend (`codecaine-python-analysis`)

### 2-1. `app/schemas/analysis.py` - 스키마 추가

```python
class ChatCalculateRequest(BaseModel):
    result_id: int                        # 기존 분석 결과 ID
    new_purpose: str | None = None        # 채팅에서 파싱한 새 목적, 없으면 null
    chat_history: list[dict] | None = None
```

---

### 2-2. `app/services/analysis_service.py` - 함수 추가

`start_chat_analysis()` 함수 구현.

```
1. result_id로 기존 분석 결과 조회 (LLM context용 — 전체 결과 포함)
   - analysis_result.summary        → step1 요약 텍스트
   - nutrient_gap (result_id 기준)  → step2 갭 목록
   - recommendation (result_id 기준) → step3 추천 목록
   → previous_analysis = {
       "summary": summary 텍스트,
       "gaps": [...],
       "recommendations": [...]
     }

2. cognito_id로 users 서비스에서 CODEF 데이터 조회 (기존과 동일)

3. DB에서 조회 (기존과 동일)
   - current_supplements (analysis_supplements)
   - unit_cache (ans_unit_convertor)
   - products (products + product_nutrients)

4. AgentCore invoke_agent_runtime 호출
   payload = {
     cognito_id, new_purpose, chat_history,
     previous_analysis,
     codef_health_data, current_supplements,
     unit_cache, products
   }

5. 분석 결과를 그대로 Chatbot Super Agent에 반환 (DB 저장 없음)
   - step1 (required_nutrients, summary)
   - step2 (gaps)
   - step3 (recommendations)
```

---

### 2-3. `app/api/endpoints/analysis.py` - 엔드포인트 추가

```python
@router.post("/chat-calculate", response_model=dict)
def chat_calculate_analysis(
    request: ChatCalculateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    cognito_id: str = Depends(get_current_user),
):
    """챗봇 재분석 전용 엔드포인트"""
```

> 인증 방식은 기존 `/calculate`와 동일하게 사용자 JWT 사용.
> Super Agent가 사용자 JWT를 그대로 forwarding.

---

## 3. 흐름 요약

```
Chatbot Super Agent
  ↓ POST /api/analysis/chat-calculate
  { result_id, new_purpose(optional), chat_history }

Analysis Backend
  ↓ result_id → DB에서 previous_analysis 조회
  ↓ cognito_id → users 서비스에서 CODEF 조회
  ↓ DB에서 supplements/unit_cache/products 조회
  ↓ invoke_agent_runtime

Analysis Agent
  ↓ new_purpose 있으면 새 목적으로 Step1
  ↓ new_purpose 없으면 previous_analysis의 기존 목적으로 Step1
  ↓ Step2 (Lambda)
  ↓ Step3 (LLM)

Analysis Backend
  ↓ 분석 결과 JSON 그대로 반환 (DB 저장 없음)

Chatbot Super Agent
```

---

## 4. 응답 구조 (`response_model=dict`)

```json
{
  "cognito_id": "...",
  "step1": {
    "required_nutrients": [
      { "name_ko": "비타민 D", "name_en": "Vitamin D", "rda_amount": 800, "unit": "IU", "reason": "..." }
    ],
    "summary": {
      "overall_assessment": "...",
      "key_concerns": ["..."],
      "lifestyle_notes": "..."
    }
  },
  "step2": {
    "gaps": [
      { "name_ko": "비타민 D", "current_amount": 200, "gap_amount": 600, "unit": "IU" }
    ]
  },
  "step3": {
    "recommendations": [
      { "rank": 1, "product_id": 12, "product_name": "...", "product_brand": "...", "recommend_serving": 2, "covered_nutrients": ["비타민 D"] }
    ]
  }
}
```

> `response_model=dict` 사용 — Pydantic 검증 없음. Super Agent는 `step3.recommendations` 및 `step1.summary`를 챗봇 응답 생성에 활용.

---

## 5. 기존 `/calculate`와 차이점

| | `/calculate` | `/chat-calculate` |
|---|---|---|
| 호출자 | 프론트엔드 | Chatbot Super Agent |
| 분석 목적 입력 | `purposes` (프론트 직접 입력) | `new_purpose` (채팅 파싱) or null |
| 이전 분석 결과 | 없음 | `result_id`로 DB 조회 |
| 채팅 내역 | 없음 | `chat_history` 전달 |
| 건강 데이터 | 프론트 `health_check_data` or CODEF | CODEF만 (프론트 없음) |
