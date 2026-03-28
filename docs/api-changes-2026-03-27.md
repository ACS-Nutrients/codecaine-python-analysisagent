# API 변경사항 이력 (2026-03-27)

Analysis 기능 구현 과정에서 변경/추가된 API 목록.
User 서비스 개발자는 아래 내용을 확인해 변경된 흐름을 파악할 수 있음.

---

## User 서비스 변경사항

### 신규: `POST /users/{cognito_id}/condition-snapshot`

프론트에서 사용자가 입력한 분석 목적을 `user_condition_snapshots` 테이블에 저장.

- **인증**: JWT 필수
- **저장**: `purposes` 배열 → `", "` join → `user_condition_snapshots.status`
- **호출 시점**: 프론트가 분석 시작(`/analysis/calculate`) 직전에 호출

```
요청: POST /users/{cognito_id}/condition-snapshot
      { "purposes": ["피로 회복", "면역 강화"] }

응답: { "success": true, "message": "현재 상태가 저장되었습니다." }
```

**추가된 파일**:
- `app/api/endpoints/users.py` → `save_condition_snapshot()` 엔드포인트
- `app/services/user_service.py` → `UserService.save_condition_snapshot()`
- `app/schemas/user.py` → `ConditionSnapshotRequest`, `ConditionSnapshotResponse`

---

### 신규: `GET /users/codef/internal-service/{cognito_id}`

Analysis Backend의 `/chat-calculate`가 JWT 없이 VPC 내부에서 CODEF 데이터를 가져오기 위한 서비스 간 전용 엔드포인트.

- **인증**: 없음 — VPC 내부 전용
- **반환 데이터**: 기존 `/internal-call/{cognito_id}`와 동일 구조
- **호출 주체**: Analysis Backend (`codecaine-python-analysis`)

```
요청: GET /users/codef/internal-service/{cognito_id}

응답:
{
  "codef_health_data": { "혈압(수축기)": "120", "공복혈당": "95", ... },
  "medication_info":   [ { "name": "아스피린", "dose": "100mg", "usage": "해열/진통/소염제" } ]
}
```

**⚠️ 외부 노출 차단 필요**: API Gateway / ALB에서 `/users/codef/internal-service/*` 경로 차단.

**추가된 파일**:
- `app/api/endpoints/codef.py` → `get_internal_service_data()` 엔드포인트

---

## Analysis Backend 변경사항

### 수정: `POST /analysis/calculate`

- CODEF 데이터를 user 서비스에서 직접 조회하던 방식 → **프론트가 직접 전달하는 방식으로 변경**
- `health_check_data.exam_items` 필드 추가 (건강검진 주요 항목 배열)
- `prescription_data` 필드 추가 (처방 약물 목록)

```
요청 바디:
{
  "health_check_data": {
    "exam_date": "2024-01-15", "gender": 1, "age": 35, "height": 175.0, "weight": 70.0,
    "exam_items": [ { "name": "혈압(수축기)", "value": "120", "unit": "mmHg" } ]
  },
  "prescription_data": [ { "name": "아스피린", "dose": "100mg", "usage": "해열/진통/소염제" } ],
  "purposes": ["피로 회복"]
}
```

**수정된 파일**:
- `app/schemas/analysis.py` → `ExamItem`, `PrescriptionItem` 추가, `HealthCheckData`/`AnalysisCalculateRequest` 수정
- `app/services/analysis_service.py` → `start_analysis()` token 파라미터 제거, `_upsert_intake_purpose()` 제거
- `app/api/endpoints/analysis.py` → `calculate_analysis()` 수정

---

### 신규: `POST /analysis/chat-calculate`

Chatbot Super Agent가 재분석 요청 시 사용. DB 저장 없이 결과 JSON 반환.

- **인증**: 없음 (`cognito_id`는 바디로 전달)
- **CODEF 조회**: User 서비스 `/internal-service/{cognito_id}` 내부 호출 (JWT 없음)
- **결과**: DB 저장 안 함, 바로 반환

```
요청: POST /analysis/chat-calculate
{
  "cognito_id": "...",
  "result_id": 123,
  "new_purpose": "관절 건강",
  "chat_history": [ { "role": "user", "content": "..." } ]
}

응답: { "step1": {...}, "step2": {...}, "step3": {...} }
```

> `step1.summary`에는 LLM 생성 필드 외에 아래 컨텍스트 필드가 추가로 주입됨:
> - `purpose`: 재분석 목적 (`new_purpose`, 없으면 빈 문자열)
> - `medications`: 복용 약물 이름 목록 (CODEF에서 조회)
> - `supplements`: 섭취 중인 영양제 이름 목록 (`analysis_supplements` DB 조회)

```
```

**추가된 파일**:
- `app/schemas/analysis.py` → `ChatCalculateRequest`
- `app/services/analysis_service.py` → `start_chat_analysis()`
- `app/services/user_client.py` → `get_codef_data_internal()`
- `app/api/endpoints/analysis.py` → `chat_calculate_analysis()`

---

## Analysis Agent 변경사항

### 수정: `AnalysisRequest` 스키마

`new_purpose`, `chat_history`, `previous_analysis` 필드 추가 (재분석 컨텍스트 전달용).

- **수정된 파일**: `app/schemas/analysis.py`, `app/services/analysis_agent.py`

---

## Frontend 변경사항

### 수정: `startAnalysis` — `exam_items`, `prescription_data` 필드 추가

### 신규: `saveConditionSnapshot` — 분석 목적 저장 API 호출 추가

- 분석 시작 직전에 `POST /users/{cognito_id}/condition-snapshot` 호출
- **수정된 파일**: `src/app/api.ts`, `src/app/pages/Recommendation.tsx`

---

## 전체 호출 흐름 요약

```
[프론트]
  → POST /users/{id}/condition-snapshot         (목적 저장, JWT)
  → POST /analysis/calculate                    (분석, JWT, CODEF 직접 전달)

[Chatbot Super Agent]
  → POST /analysis/chat-calculate               (JWT 없음)
      └→ GET /users/codef/internal-service/{id} (VPC 내부, JWT 없음)
```
