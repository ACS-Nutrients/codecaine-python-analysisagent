# LLM Agent API 테스트 방법론 — 2026-03-27

## 개요

일반 REST API와 LLM 기반 에이전트 API의 테스트는 접근 방식이 다르다.
일반 API는 입력 → 출력이 결정적(deterministic)이지만,
LLM 에이전트는 같은 입력에도 출력이 매번 달라질 수 있다.

이 문서는 `analysis-agent`(`/invocations`) 엔드포인트를 기준으로
실제로 우리가 수행한 테스트 과정과 방법론을 정리한다.

---

## 테스트 레이어 구조

```
┌─────────────────────────────────┐
│  Layer 3. 성능/품질 평가         │  LLM 출력 품질 자체 평가
│  (Eval / LLM-as-Judge)          │
├─────────────────────────────────┤
│  Layer 2. 통합 테스트            │  서비스 간 연동 확인
│  (E2E / Integration)            │  (analysis backend → agent → Lambda)
├─────────────────────────────────┤
│  Layer 1. 로컬 기능 테스트       │  엔드포인트 동작 확인 ← 오늘 진행
│  (Local Functional Test)        │
└─────────────────────────────────┘
```

---

## Layer 1. 로컬 기능 테스트 (오늘 진행한 방식)

### 목적

- 스키마 검증 (Pydantic 422 오류 없는지)
- Step1 → Step2 → Step3 파이프라인 전체 흐름 동작 확인
- 각 Step의 output 구조가 의도한 형태인지 확인
- 약물-영양소 상호작용, previous_analysis 컨텍스트 반영 여부 확인

### 방법

```bash
# 1. 서버 로컬 실행
uvicorn app.main:app --port 8005

# 2. 요청 전송 (curl)
curl -X POST http://localhost:8005/invocations \
  -H "Content-Type: application/json" \
  -d '{...payload...}' | python3 -m json.tool

# 3. 헬스체크
curl http://localhost:8005/ping
```

### 체크리스트

- [ ] `/ping` 200 응답
- [ ] 올바른 스키마 입력 시 200 응답 (422 없음)
- [ ] step1: `required_nutrients` 배열 비어있지 않음
- [ ] step1: `summary.overall_assessment`, `key_concerns`, `lifestyle_notes` 존재
- [ ] step2: Lambda 호출 성공 (`gaps` 배열 반환)
- [ ] step3: `recommendations`가 입력한 `products` 목록 내 `product_id`만 사용
- [ ] `previous_analysis` 있을 때 → step1 reason에 이전 분석 맥락 반영됨

### 실제 트러블슈팅 (오늘)

| 문제 | 원인 | 해결 |
|---|---|---|
| 포트 충돌 (8001) | 다른 서비스가 선점 | 8005로 변경 |
| `ModuleNotFoundError: chromadb` | `.venv` shebang이 다른 경로 참조 (깨진 venv) | 별도 venv 직접 지정하여 실행 |
| OpenAI 401 | `.env` API 키 만료 | `LLM_PROVIDER=bedrock`으로 전환 |
| Lambda `ResourceNotFoundException` | `.env`의 함수명 불일치 (`action-nutrient-calc`) | `cdci-prd-nutrient-calc`으로 수정 |

---

## Layer 2. 통합 테스트 (E2E)

### 목적

실제 배포 환경에서 서비스 간 연동이 올바르게 동작하는지 확인.

```
Frontend → API Gateway → analysis backend → AgentCore → Lambda
                                          → User service (VPC internal)
```

### 방법

배포 후 실제 API Gateway 엔드포인트로 요청:

```bash
curl -X POST https://api.codecaine.store/analysis/calculate \
  -H "Authorization: Bearer {JWT}" \
  -H "Content-Type: application/json" \
  -d '{
    "purpose": "피로 회복",
    "health_check_data": {},
    "prescription_data": []
  }'
```

### 체크리스트

- [ ] JWT 인증 통과
- [ ] analysis backend → AgentCore 호출 성공 (CloudWatch 로그 확인)
- [ ] AgentCore → Lambda 호출 성공
- [ ] DB 저장 확인 (`analysis_result`, `nutrient_gap`, `recommendation` 테이블)
- [ ] `nutrient_gap` 레코드에 `nutrient_id` 정상 매핑 (resolve_nutrient_ids 동작)

---

## Layer 3. LLM 출력 품질 평가

### 일반 API와의 차이

| 항목 | 일반 API | LLM API |
|---|---|---|
| 출력 | 결정적 (동일 입력 = 동일 출력) | 비결정적 (매번 달라질 수 있음) |
| 테스트 기준 | exact match | 의미적 정확성, 구조 일관성 |
| 자동화 | 쉬움 | 별도 eval 프레임워크 필요 |

### 방법 1 — Eval 셋 기반 정량 평가

다양한 케이스를 미리 정의하고 기대 출력과 실제 출력을 비교.

```python
# 테스트 케이스 예시
eval_cases = [
    {
        "name": "비타민D 결핍 케이스",
        "input": {"codef_health_data": {"vitamin_d": 10.0}, ...},
        "expected": {
            "required_nutrients_includes": ["비타민D"],
            "step3_product_ids_valid": True,  # products 목록 내 ID만 사용
        }
    },
    {
        "name": "약물 상호작용 케이스 (와파린)",
        "input": {"medication_info": [{"name": "와파린"}], ...},
        "expected": {
            "lifestyle_notes_contains": "비타민K",  # 상호작용 언급 여부
        }
    }
]
```

실무 도구: **AWS Bedrock Model Evaluation**, **LangSmith**, **RAGAS**

### 방법 2 — LLM-as-Judge

다른 LLM(예: Claude Opus)에게 출력 품질을 평가하도록 요청.

```python
judge_prompt = """
다음 영양제 추천 결과를 평가해주세요.

[입력 건강 데이터]
{health_data}

[추천 결과]
{recommendations}

평가 기준:
1. 건강 데이터와 추천의 연관성 (1-5점)
2. 약물 상호작용 고려 여부 (yes/no)
3. 추천 이유의 명확성 (1-5점)
"""
```

### 방법 3 — 사람 평가 (Gold Standard)

영양사 또는 의사가 직접 결과 검토. 정확도는 가장 높지만 비용이 큼.
초기 서비스 런칭 전 샘플 케이스 검토에 활용.

---

## 오늘 수행한 테스트 결과 요약

### 테스트 케이스 1 — 일반 분석 (calculate)

**Input 요약:** 35세 남성, 비타민D 18.0, 페리틴 20.0, 오메프라졸 복용, 오메가3 섭취 중

**결과: ✅ 통과**

- Step1: 비타민D, 철분, 비타민C, 비타민B복합체, 아연 5개 영양소 도출
- Step2: Lambda 정상 호출, 전체 gap 계산 완료
- Step3: 입력한 3개 제품 중 3개 추천 (products 목록 내에서만 선택)
- 오메프라졸 → 비타민B12 흡수 저하 상호작용 `lifestyle_notes`에 반영됨 ✅

---

### 테스트 케이스 2 — 챗봇 재분석 (chat-calculate, previous_analysis 포함)

**Input 요약:** 케이스 1 이후, `new_purpose="수면 개선에 집중"`, 이전 분석 결과(비타민D/철분) 포함

**결과: ✅ 통과**

- Step1: `previous_analysis.gaps`의 `name_ko`("비타민D", "철분")를 읽어 기존 영양소 유지
- 새 목적(수면 개선)에 맞게 마그네슘, 메라토닌 추가 도출
- `previous_analysis.recommendations`의 `product_name` 반영 — 이전 추천과 달라진 이유 `reason`에 명시
- Step3: 수면 관련 제품(마그네슘, L-테아닌) 우선 추천으로 변경됨 ✅

**핵심 검증:** `previous_analysis`에 `name_ko`, `product_name`이 포함되면서 LLM이 이전 맥락을 올바르게 이해하고 재분석에 활용함을 확인.

---

## Input 스키마 (`AnalysisRequest`) 빠른 참조

```
cognito_id              str
intake_purpose          str | None          일반 분석용 섭취 목적
new_purpose             str | None          챗봇 재분석용 새 목적
user_profile            dict | None         {birth_dt, gender, height, weight, allergies, chron_diseases, current_conditions}
codef_health_data       dict | None         CODEF 건강검진 JSON
medication_info         list[dict] | None   [{name, dosage, frequency}]
current_supplements     list[dict] | None   [{product_name, serving_per_day, ingredients: [{name, amount}]}]
unit_cache              dict | None         {영양소명: 변환계수}
products                list[dict] | None   [{product_id, product_name, product_brand, serving_per_day, nutrients: [{name_ko, name_en, amount_per_day}]}]
chat_history            list[dict] | None   [{role, content}]
previous_analysis       dict | None         {summary, gaps: [{nutrient_id, name_ko, current_amount, gap_amount, unit}], recommendations: [{product_id, product_name, product_brand, rank, recommend_serving}]}
```
