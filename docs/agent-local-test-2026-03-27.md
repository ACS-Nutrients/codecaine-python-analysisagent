# Analysis Agent 로컬 테스트 — 2026-03-27

## 테스트 목적

`/invocations` 엔드포인트에 실제 데이터 구조를 입력해 Step1 → Step2 → Step3 파이프라인이 정상 동작하는지 확인.

---

## 환경 설정

| 항목 | 값 |
|---|---|
| LLM Provider | AWS Bedrock (`anthropic.claude-3-5-sonnet-20240620-v1:0`) |
| Lambda (Step2) | `cdci-prd-nutrient-calc` |
| 서버 포트 | 8005 |
| venv | `/Users/jisu/Code/ACS/PJT-3/llm-analysis/analysis-agent-ver1/.venv` |

> **참고:** `.env`의 `LLM_PROVIDER`를 `bedrock`으로, `LAMBDA_FUNCTION_NAME`을 `cdci-prd-nutrient-calc`으로 변경해 테스트.

---

## 테스트 케이스 1 — 일반 분석 (calculate)

### Input (`AnalysisRequest` 스키마 기준)

```json
{
  "cognito_id": "test-user-001",
  "intake_purpose": "피로 회복 및 면역력 강화",
  "new_purpose": null,
  "user_profile": {
    "birth_dt": "1990-05-15",
    "gender": "M",
    "height": 175.0,
    "weight": 72.0,
    "allergies": null,
    "chron_diseases": null,
    "current_conditions": null
  },
  "codef_health_data": {
    "hemoglobin": 13.5,
    "vitamin_d": 18.0,
    "ferritin": 20.0
  },
  "medication_info": [
    {"name": "오메프라졸", "dosage": "20mg", "frequency": "1일 1회"}
  ],
  "current_supplements": [
    {
      "product_name": "오메가3",
      "serving_per_day": 1,
      "ingredients": [
        {"name": "EPA", "amount": 300.0},
        {"name": "DHA", "amount": 200.0}
      ]
    }
  ],
  "unit_cache": {"비타민D": "0.025", "비타민C": "1.0"},
  "products": [
    {
      "product_id": 1,
      "product_name": "종근당 비타민D 2000IU",
      "product_brand": "종근당",
      "serving_per_day": 1,
      "nutrients": [
        {"name_ko": "비타민D", "name_en": "Vitamin D", "amount_per_day": 50.0}
      ]
    },
    {
      "product_id": 2,
      "product_name": "뉴트리원 철분 플러스",
      "product_brand": "뉴트리원",
      "serving_per_day": 1,
      "nutrients": [
        {"name_ko": "철분", "name_en": "Iron", "amount_per_day": 14.0}
      ]
    },
    {
      "product_id": 3,
      "product_name": "고려은단 비타민C 1000",
      "product_brand": "고려은단",
      "serving_per_day": 1,
      "nutrients": [
        {"name_ko": "비타민C", "name_en": "Vitamin C", "amount_per_day": 1000.0}
      ]
    }
  ],
  "chat_history": null,
  "previous_analysis": null
}
```

### Output

**Step1 — 필요 영양소 분석**

| name_ko | name_en | rda_amount | unit | reason 요약 |
|---|---|---|---|---|
| 비타민 D | Vitamin D | 800 | IU | 검진 수치 18.0 ng/mL (정상 30~50) |
| 철분 | Iron | 10 | mg | 페리틴 20.0 ng/mL (정상 30~300) |
| 비타민 C | Vitamin C | 1000 | mg | 면역력 강화 + 철분 흡수 촉진 |
| 비타민 B 복합체 | Vitamin B Complex | 1 | 정 | 피로 회복 및 에너지 대사 |
| 아연 | Zinc | 15 | mg | 면역력 강화 |

**Step1 — Summary**

- overall_assessment: 비타민D와 철분 수치가 낮으며, 피로 회복과 면역력 강화가 필요한 상태
- key_concerns: 비타민D 결핍, 철분 부족, 피로 및 면역력 약화
- lifestyle_notes: 오메프라졸 복용 시 비타민 B12 흡수 저하 가능, 햇빛 노출 및 철분 풍부 식품 섭취 권장

**Step2 — 영양소 갭 (Lambda 계산)**

| name_ko | current_amount | gap_amount | rda_amount | unit |
|---|---|---|---|---|
| 비타민 D | 0 | 800 | 800 | mg |
| 철분 | 0 | 10 | 10 | mg |
| 비타민 C | 0 | 1000 | 1000 | mg |
| 비타민 B 복합체 | 0 | 1 | 1 | mg |
| 아연 | 0 | 15 | 15 | mg |

**Step3 — 영양제 추천**

| rank | product_id | product_name | brand | recommend_serving | covered_nutrients |
|---|---|---|---|---|---|
| 1 | 1 | 종근당 비타민D 2000IU | 종근당 | 1 | 비타민 D |
| 2 | 2 | 뉴트리원 철분 플러스 | 뉴트리원 | 1 | 철분 |
| 3 | 3 | 고려은단 비타민C 1000 | 고려은단 | 1 | 비타민 C |

### 결과: ✅ 정상

---

## 트러블슈팅 내역

| 문제 | 원인 | 해결 |
|---|---|---|
| 포트 8001 충돌 | 다른 프로세스가 선점 | 8005 포트로 변경 |
| `ModuleNotFoundError: chromadb` | `.venv` shebang이 다른 경로 참조 (깨진 venv) | `llm-analysis` venv 사용 |
| OpenAI 401 오류 | `.env`의 OpenAI API 키 만료 | `LLM_PROVIDER=bedrock`으로 전환 |
| Lambda `ResourceNotFoundException` | `.env`의 `LAMBDA_FUNCTION_NAME`이 `action-nutrient-calc`으로 잘못 설정 | `cdci-prd-nutrient-calc`으로 수정 |

---

## Input 데이터 타입 정리 (`AnalysisRequest` 스키마)

```
cognito_id              str
intake_purpose          str | None          — 일반 분석용 섭취 목적
new_purpose             str | None          — 챗봇 재분석용 새 목적
user_profile            dict | None         — birth_dt, gender, height, weight, allergies, chron_diseases, current_conditions
codef_health_data       dict | None         — CODEF 건강검진 JSON (항목명: 수치)
medication_info         list[dict] | None   — [{name, dosage, frequency}]
current_supplements     list[dict] | None   — [{product_name, serving_per_day, ingredients: [{name, amount}]}]
unit_cache              dict | None         — {영양소명: 변환계수}
products                list[dict] | None   — [{product_id, product_name, product_brand, serving_per_day, nutrients: [{name_ko, name_en, amount_per_day}]}]
chat_history            list[dict] | None   — [{role, content}]
previous_analysis       dict | None         — {summary, gaps: [{nutrient_id, name_ko, ...}], recommendations: [{product_id, product_name, ...}]}
```
