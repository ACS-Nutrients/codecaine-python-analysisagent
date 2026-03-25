# LLM Agent 계정 변경 가이드

계정 변경 후 재배포 시 수정해야 할 파일과 항목 목록입니다.

---

## 1. `.env` (최우선)

```env
AWS_REGION=ap-northeast-2           # 리전 변경 시 수정

OPENAI_API_KEY=sk-proj-...          # → 새 OpenAI API Key로 교체
OPENAI_MODEL_ID=gpt-4o              # 모델 변경 시 수정

ANTHROPIC_API_KEY=sk-ant-...        # → 새 Anthropic API Key로 교체
ANTHROPIC_MODEL_ID=claude-sonnet-4-5

BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20240620-v1:0  # 모델 변경 시 수정

AWS_ACCESS_KEY_ID=AKIA...           # → 새 AWS Access Key로 교체
AWS_SECRET_ACCESS_KEY=...           # → 새 AWS Secret Key로 교체
```

---

## 2. `.github/workflows/deploy.yml`

```yaml
env:
  AWS_REGION: ap-northeast-2
  AWS_ACCOUNT_ID: 070238434919          # → 새 AWS 계정 ID로 교체
  ECR_REPOSITORY: analysis-agent        # ECR 리포 이름 변경 시 수정
  AGENTCORE_RUNTIME_ID: analysis_agent-bA2YOVDhUj  # → 새 AgentCore Runtime ID로 교체
  AGENTCORE_ROLE_ARN: arn:aws:iam::070238434919:role/agentcore-runtime-role
  # └─ AWS_ACCOUNT_ID 바꾸면 ARN도 같이 수정 필요
```

---

## 3. `tmp/invoke_test.py` (로컬 테스트 시)

```python
agentRuntimeArn="arn:aws:bedrock-agentcore:ap-northeast-2:070238434919:runtime/analysis_agent-bA2YOVDhUj"
# → 새 계정 ID + 새 Runtime ID로 교체
```

---

## 변경 항목 체크리스트

| 완료 | 파일 | 항목 |
|:---:|---|---|
| [ ] | `.env` | `AWS_ACCESS_KEY_ID` |
| [ ] | `.env` | `AWS_SECRET_ACCESS_KEY` |
| [ ] | `.env` | `OPENAI_API_KEY` |
| [ ] | `.env` | `ANTHROPIC_API_KEY` (사용 시) |
| [ ] | `.github/workflows/deploy.yml` | `AWS_ACCOUNT_ID` |
| [ ] | `.github/workflows/deploy.yml` | `AGENTCORE_RUNTIME_ID` |
| [ ] | `.github/workflows/deploy.yml` | `AGENTCORE_ROLE_ARN` |
| [ ] | `tmp/invoke_test.py` | Bedrock AgentCore ARN |

---

## 참고

- `app/core/config.py` — `.env` 값으로 오버라이드되므로 **별도 수정 불필요**
- `scripts/setup_oidc.py` — 계정 ID를 `boto3`로 동적 조회하므로 **별도 수정 불필요**
- AgentCore Runtime ID는 새 계정에 AgentCore를 배포한 후 발급된 값으로 교체
