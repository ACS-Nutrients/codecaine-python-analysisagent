# previous_analysis 맥락 파악 테스트

> `new_purpose`가 없을 때 LLM이 `previous_analysis.summary`에서 기존 목적을 올바르게 파악하는지 검증

---

## 테스트 시나리오

| 케이스 | new_purpose | intake_purpose | previous_analysis | 기대 동작 |
|---|---|---|---|---|
| A | 있음 | 있음 | 있음 | `new_purpose` 우선 사용 |
| B | 없음 | 있음 | 없음 | `intake_purpose` 사용 |
| C | 없음 | 없음 | 있음 | summary에서 기존 목적 파악 ← **검증 대상** |
| D | 없음 | 없음 | 없음 | 목적 없이 일반 분석 |

---

## 케이스 C 상세 검증

### 입력 payload

```json
{
  "cognito_id": "test-user-001",
  "intake_purpose": null,
  "new_purpose": null,
  "previous_analysis": {
    "summary": "[섭취 목적] 피로 회복\n[복용 약물] 없음\n[전반적 평가] 전반적으로 양호\n[주요 우려사항] 없음\n[생활습관] 운동 부족\n[필요 영양소] 비타민 B12 500mcg",
    "gaps": [],
    "recommendations": []
  },
  "codef_health_data": {},
  "medication_info": [],
  "current_supplements": [],
  "unit_cache": {},
  "products": []
}
```

### 검증 기준

LLM이 반환한 `required_nutrients`와 `summary`에서 아래를 확인:

1. **목적 반영 여부** — "피로 회복"과 관련된 영양소(비타민 B군, 철분, 마그네슘 등)가 `required_nutrients`에 포함되어야 함
2. **reason 필드** — 각 영양소의 `reason`에 "이전 분석" 또는 "재분석" 맥락 언급 여부
3. **목적 무시 케이스 방지** — 피로 회복과 무관한 영양소만 추천되면 실패

---

## 테스트 방법

### 로컬 컨테이너 실행 후 직접 호출

```bash
# 1. 컨테이너 실행
docker run --rm -p 8080:8080 \
  -e LLM_PROVIDER=anthropic \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e LAMBDA_FUNCTION_NAME=cdci-prd-nutrient-calc \
  <image>

# 2. /invocations 직접 호출
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "cognito_id": "test-user-001",
    "intake_purpose": null,
    "new_purpose": null,
    "previous_analysis": {
      "summary": "[섭취 목적] 피로 회복\n[복용 약물] 없음\n[전반적 평가] 전반적으로 양호\n[주요 우려사항] 없음\n[생활습관] 운동 부족\n[필요 영양소] 비타민 B12 500mcg",
      "gaps": [],
      "recommendations": []
    },
    "codef_health_data": {},
    "medication_info": [],
    "current_supplements": [],
    "unit_cache": {},
    "products": []
  }'
```

### AgentCore invoke로 호출 (배포 후)

`tmp/invoke_test.py` 수정해서 위 payload로 테스트.

---

---

## 서비스 배포 후 통합 테스트 (chat-calculate 엔드포인트)

### 테스트 목적
`/api/analysis/chat-calculate` 실제 호출 시 아래 두 가지가 정상 동작하는지 확인.

1. **기존 분석 결과 DB 조회** — `result_id`로 `analysis_result`, `nutrient_gap`, `recommendation` 조회가 올바르게 되는지
2. **user 서비스 CODEF 조회** — `cognito_id` + JWT로 user 서비스 `/api/users/codef/internal-call/{cognito_id}` 호출이 성공하는지

### 확인 항목

| 항목 | 확인 방법 | 기대 결과 |
|---|---|---|
| DB 조회 | 실제 존재하는 `result_id` 사용 | `previous_analysis.summary` 값이 DB의 `analysis_result.summary`와 동일 |
| DB 조회 | 존재하지 않는 `result_id` 사용 | 404 에러 반환 |
| CODEF 조회 | 유효한 JWT + CODEF 데이터 있는 사용자 | `codef_health_data`, `medication_info` 정상 포함되어 분석 |
| CODEF 조회 | CODEF 데이터 없는 사용자 | 빈 값으로 분석 계속 진행 (에러 아님) |
| CODEF 조회 | user 서비스 다운 시 | 빈 값으로 분석 계속 진행 (에러 아님) |

### 호출 예시

```bash
curl -X POST https://<analysis-backend>/api/analysis/chat-calculate \
  -H "Authorization: Bearer <사용자 JWT>" \
  -H "Content-Type: application/json" \
  -d '{
    "result_id": 1,
    "new_purpose": "관절 건강",
    "chat_history": [
      {"role": "user", "content": "관절이 아파서 영양제 추천받고 싶어요"}
    ]
  }'
```

### 로그 확인 포인트

```
# user 서비스 CODEF 조회 성공 시
INFO  [cognito_id] AgentCore 호출 성공

# user 서비스 조회 실패 시 (분석은 계속 진행)
WARNING [cognito_id] user 서비스 CODEF 데이터 조회 실패 (HTTP 4xx) — 빈 값으로 진행
```

---

## 주의사항

- `섭취 목적: ` 줄이 빈 문자열로 들어가므로 LLM이 `previous_analysis.summary`의 `[섭취 목적]` 항목을 읽어야 함
- LLM이 이를 무시하고 일반 분석을 하면 SYSTEM_PROMPT_STEP1 재분석 지시 보강 필요
- 케이스 A도 함께 테스트해서 `new_purpose`가 `previous_analysis` 목적을 올바르게 override하는지 확인
