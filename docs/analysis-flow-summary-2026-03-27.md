# Analysis 시스템 흐름 정리 (2026-03-27)

---

## 1. `/calculate` — 일반 분석 (프론트 → Analysis Backend)

### 호출자: 프론트엔드

**프론트가 Analysis Backend에 보내는 것:**
```json
POST /api/analysis/calculate
Authorization: Bearer <JWT>

{
  "health_check_data": {
    "exam_date", "gender", "age", "height", "weight",
    "exam_items": [{ "name", "value", "unit" }]   ← CODEF 건강검진 항목
  },
  "prescription_data": [{ "name", "dose", "usage" }],  ← CODEF 처방 약물
  "purposes": ["피로 회복", "면역 강화"]
}
```

> CODEF 정보는 프론트에서 직접 받음 (user 서비스 호출 없음)

### Analysis Backend 내부 처리

```
1. JWT로 cognito_id 추출
2. analysis_userdata DB 조회 (DMS 연동 데이터)
   - 성별, 나이, 키, 상태정보, 기저질환, 알레르기 → user_profile
3. AgentCore 호출
   payload = {
     cognito_id, intake_purpose,
     user_profile,          ← DMS 연동 유저 정보
     codef_health_data,     ← 프론트에서 받은 건강검진 수치
     medication_info,       ← 프론트에서 받은 처방 약물
     current_supplements,   ← DB 조회 (analysis_supplements)
     unit_cache,            ← DB 조회 (ans_unit_convertor)
     products               ← DB 조회 (products + product_nutrients)
   }
4. 결과 DB 저장 (analysis_result, nutrient_gap, recommendation)
5. result_id 반환
```

---

## 2. `/chat-calculate` — 챗봇 재분석 (Chatbot Super Agent → Analysis Backend)

### 호출자: Chatbot Super Agent

**Super Agent가 Analysis Backend에 보내는 것:**
```json
POST /api/analysis/chat-calculate
(JWT 인증 없음)

{
  "cognito_id": "...",
  "result_id": 123,                        ← 기존 분석 결과 ID
  "new_purpose": "관절 건강",               ← 채팅에서 파싱한 새 목적 (없으면 null)
  "chat_history": [                        ← 채팅 대화 내역
    { "role": "user", "content": "..." }
  ]
}
```

> `new_purpose` = 이미지의 "현재상태" (채팅에서 언급된 경우 Super Agent가 파싱해서 전달)

### Analysis Backend 내부 처리

```
1. result_id + cognito_id로 기존 분석 결과 DB 조회
   - analysis_result.summary      → 기존 분석 목적 포함된 텍스트
   - nutrient_gap (result_id)     → 기존 갭 목록
   - recommendation (result_id)   → 기존 추천 목록
   → previous_analysis = { summary, gaps, recommendations }

   ※ 이미지: "기존 분석 결과의 분석 목적 (분석 결과 id로 조회 후 결과에서 파싱)"
     → summary 텍스트 안에 "[섭취 목적] ..." 형태로 포함되어 있고
       new_purpose가 없으면 LLM이 summary에서 목적 파악

2. user 서비스 내부 엔드포인트로 CODEF 조회 (JWT 없이 VPC 내부 호출)
   - GET /users/codef/internal-service/{cognito_id}
   - codef_health_data, medication_info

3. AgentCore 호출
   payload = {
     cognito_id,
     new_purpose,           ← 새 목적 (없으면 null)
     chat_history,          ← 채팅 대화 내역
     previous_analysis,     ← 기존 분석 결과 (LLM context용)
     codef_health_data,     ← user 서비스에서 조회
     medication_info,       ← user 서비스에서 조회
     current_supplements,   ← DB 조회
     unit_cache,            ← DB 조회
     products               ← DB 조회
   }

4. 결과 그대로 반환 (DB 저장 없음)
```

---

## 3. Analysis Agent 내부 처리 (공통)

```
Step1 (LLM)
  - purpose = new_purpose or intake_purpose or ""
  - purpose 없으면 LLM이 previous_analysis.summary에서 기존 목적 파악
  - previous_analysis 있으면 이전 추천과 달라진 이유를 reason에 명시
  → required_nutrients, summary 반환

Step2 (Lambda: cdci-prd-nutrient-calc)
  - required_nutrients + current_supplements + unit_cache
  → gaps 반환

Step3 (LLM)
  - gaps + products
  → recommendations 반환
```

---

## 4. 이미지 반영 여부 확인

| 이미지 내용 | 반영 여부 | 비고 |
|---|---|---|
| Analysis 서비스: CODEF 정보는 프론트에서 받음 | ✅ | `health_check_data` + `prescription_data` |
| Analysis 서비스: 유저정보는 DB(DMS 연동)에서 | ✅ | `_get_userdata()` → `user_profile` |
| Chatbot 서비스: 프론트 데이터 없음 (X) | ✅ | JWT 없음, 프론트 데이터 없음 |
| Chatbot 서비스: CODEF는 user 서비스에서 조회 | ✅ | `get_codef_data_internal(cognito_id)` — JWT 없이 VPC 내부 호출 |
| Chatbot 서비스: 기존 분석 목적은 result에서 파싱 | ✅ | `previous_analysis.summary`에서 LLM 파악 |
| Chatbot 서비스: 채팅 내용/현재상태 전달 | ✅ | `chat_history`, `new_purpose` |
| Analysis Agent에 전달: CODEF, 기존/새 목적, 채팅 대용, 기존 분석 결과 | ✅ | payload에 모두 포함 |
| 결과: Step1, 2, 3 양식 | ✅ | `AnalysisResponse` |

---

## 5. 미완료 항목

| 항목 | 내용 |
|---|---|
| LLM 테스트 | `new_purpose` 없을 때 LLM이 `previous_analysis.summary`에서 목적 파악하는지 검증 필요 → `docs/test-previous-analysis-context.md` 참고 |
| `usage` 필드명 | 프론트 `MedItem.schedule` → `usage`로 변경, 팀원 확인 필요 |
| DMS 설정 | 1-2/1-3 서비스가 2-9 DB에 DMS 되도록 설정 필요 |
| 보안 설정 | User 서비스 `/internal-service/{cognito_id}` — API Gateway/ALB에서 외부 노출 차단 필요 |
