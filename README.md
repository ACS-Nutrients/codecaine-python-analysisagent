# Analysis Agent 사용 가이드

---

## 개요

Analysis Agent는 AWS Bedrock AgentCore Runtime에 배포된 영양소 분석 및 영양제 추천 Agent입니다.
App 서비스에서 `invoke_agent_runtime()`으로 호출하면 3단계 분석 결과를 반환합니다.

```
App → invoke_agent_runtime(payload)
        └─► Step 1: LPI KB 기반 의약품 상호작용 분석 + 필요 영양소 도출
        └─► Step 2: Lambda 갭 계산 (IU→mg 변환 포함)
        └─► Step 3: 실제 DB products 기반 추천
        └─► JSON 응답 반환
```

---

## 호출 방법

### 필요 권한

App 서비스의 IAM Role에 아래 권한 추가 필요:
```json
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": "arn:aws:bedrock-agentcore:ap-northeast-2:070238434919:runtime/*"
}
```

### App .env 설정

```env
AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-northeast-2:070238434919:runtime/analysis_agent-bA2YOVDhUj
AWS_REGION=ap-northeast-2
```

### 호출 코드

```python
import boto3
import json

client = boto3.client("bedrock-agentcore", region_name="ap-northeast-2")

response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:ap-northeast-2:070238434919:runtime/analysis_agent-bA2YOVDhUj",
    payload=json.dumps({
        "cognito_id":          "user-cognito-id",
        "intake_purpose":      "피로 회복",
        "codef_health_data":   { ... },       # CODEF 건강검진 JSON
        "medication_info":     [ ... ],        # 복용 의약품 목록
        "current_supplements": [ ... ],        # 현재 복용 영양제 (DB에서 조회)
        "unit_cache":          { ... },        # unit_convertor 테이블 전체 (DB에서 조회)
        "products":            [ ... ],        # products + product_nutrients (DB에서 조회)
    }, ensure_ascii=False)
)

result = json.loads(response["response"].read())
```

---

## 요청 payload 상세

### cognito_id `필수`
```
사용자 Cognito ID
```

### intake_purpose `필수`
```
섭취 목적 (예: "피로 회복", "면역력 강화", "뼈 건강")
```

### codef_health_data `선택`
CODEF API로 받아온 건강검진 JSON 그대로 전달.
```json
{
  "vitamin_d": 15,
  "ferritin": 10,
  "hemoglobin": 11.5
}
```

### medication_info `선택`
복용 중인 의약품 목록. KB에서 상호작용 정보 조회에 사용.
```json
[
  {"name": "와파린", "dosage": "5mg", "frequency": "1일 1회"},
  {"name": "심바스타틴", "dosage": "20mg", "frequency": "1일 1회"}
]
```

### current_supplements `선택`
현재 복용 중인 영양제. App이 `analysis_supplements` 테이블에서 조회 후 전달.
```json
[
  {
    "product_name": "비타민D3",
    "serving_per_day": 1,
    "ingredients": [
      {"name": "비타민D3", "amount": 25}
    ]
  }
]
```

### unit_cache `선택`
`unit_convertor` 테이블 전체를 App이 조회해서 전달.
Lambda가 IU → mg 변환 시 사용.
```json
{
  "비타민D": "0.000025",
  "비타민A": "0.000030",
  "비타민E": "0.00067",
  "mcg":    "0.001",
  "µg":     "0.001"
}
```

### products `선택`
Step 3 추천에 사용할 영양제 목록.
App이 `products` + `product_nutrients` 테이블 JOIN해서 전달.
**이 데이터가 없으면 Step 3 추천 결과가 빈 배열로 반환됨.**
```json
[
  {
    "product_id": 5,
    "product_name": "비타민D3 & K2",
    "product_brand": "NOW Foods",
    "serving_per_day": 1,
    "nutrients": [
      {"name_ko": "비타민D3", "unit": "mcg", "amount_per_day": 25},
      {"name_ko": "비타민K2", "unit": "mcg", "amount_per_day": 45}
    ]
  }
]
```

---

## 응답 구조

```json
{
  "cognito_id": "user-cognito-id",

  "step1": {
    "required_nutrients": [
      {
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "rda_amount": "800",
        "unit": "IU",
        "reason": "검진 결과 결핍 위험"
      }
    ],
    "summary": {
      "overall_assessment": "전반적인 영양 상태 평가",
      "key_concerns": ["우려사항1", "우려사항2"],
      "lifestyle_notes": "생활습관 메모"
    }
  },

  "step2": {
    "gaps": [
      {
        "nutrient_id": null,
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "unit": "mg",
        "current_amount": "0.0000",
        "gap_amount": "0.0200",
        "rda_amount": "0.0200"
      }
    ]
  },

  "step3": {
    "recommendations": [
      {
        "rank": 1,
        "product_id": 5,
        "product_name": "비타민D3 & K2",
        "product_brand": "NOW Foods",
        "recommend_serving": 1,
        "serving_per_day": 1,
        "covered_nutrients": ["비타민 D"]
      }
    ]
  }
}
```

---

## 응답 처리 및 DB 저장

App이 응답을 받은 후 아래 테이블에 저장:

| 응답 데이터 | 저장 테이블 |
|---|---|
| `step1.required_nutrients` + `step1.summary` | `analysis_result` |
| `step2.gaps` | `nutrient_gap` |
| `step3.recommendations` | `recommendations` |

> `step2.gaps[].nutrient_id`는 null일 수 있음.
> App에서 `name_ko`로 `nutrients` 테이블 조회해서 매핑 필요.

자세한 저장 코드는 `APP_INTEGRATION_GUIDE.md`의 `AgentCoreClient` 참고.

---

## LLM Provider 전환

Agent 내부 LLM을 바꾸려면 AgentCore Runtime 환경변수만 변경하면 됨.
App 코드 수정 불필요.

```bash
# OpenAI → Bedrock 전환 예시
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id analysis_agent-bA2YOVDhUj \
  --agent-runtime-artifact '{"containerConfiguration": {"containerUri": "070238434919.dkr.ecr.ap-northeast-2.amazonaws.com/analysis-agent:latest"}}' \
  --network-configuration '{"networkMode": "PUBLIC"}' \
  --role-arn arn:aws:iam::070238434919:role/agentcore-runtime-role \
  --environment-variables '{"LLM_PROVIDER":"bedrock","BEDROCK_MODEL_ID":"anthropic.claude-3-5-sonnet-20240620-v1:0",...}' \
  --region ap-northeast-2
```

| LLM_PROVIDER | 사용 모델 | 용도 |
|---|---|---|
| `openai` | gpt-4o | 테스트 (현재) |
| `anthropic` | claude-sonnet-4-5 | 테스트 |
| `bedrock` | Claude 3.5 Sonnet | 운영 (Bedrock 한도 승인 후) |

---

## Knowledge Base

Agent 내부에 LPI(Linus Pauling Institute) 영양소-의약품 상호작용 데이터가 포함됨.
`medication_info`를 전달하면 Step 1에서 자동으로 관련 상호작용 정보를 조회해서 분석에 활용.

**KB 데이터 업데이트 방법:**
1. 새 데이터로 Chroma DB 재생성
2. `lpi_vector_db/chroma.sqlite3`의 `config_json_str` 값 확인 (빈 값이면 수동 설정 필요 → 트러블슈팅 가이드 참고)
3. agent 레포에 복사
4. Docker 재빌드 → ECR 푸시 → AgentCore Runtime 업데이트

---

## 주의사항

- `products` payload가 비어있으면 Step 3 추천 결과가 빈 배열 반환
- `unit_cache`가 비어있으면 IU 단위 변환 불가 → mg 변환 없이 원본값 사용
- Lambda `action-nutrient-calc`가 정상 동작 중이어야 Step 2 작동
- AgentCore Runtime 환경변수 업데이트 시 `--environment-variables` 누락하면 기존 환경변수 초기화됨
