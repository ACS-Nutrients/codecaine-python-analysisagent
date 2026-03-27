# AgentCore Runtime 트러블슈팅 (2026-03-26)

## 개요

Analysis Agent (AgentCore Runtime)가 invoke 시 실패하는 문제를 디버깅한 과정 기록.

---

## 문제 1: RuntimeClientError - runtime 시작 실패

### 증상
```
RuntimeClientError: An error occurred (RuntimeClientError) when calling the InvokeAgentRuntime operation:
An error occurred when starting the runtime. Please check your CloudWatch logs for more information.
```

### 원인
`app/main.py`가 `app/services/main.py`로 이동됐으나 Dockerfile CMD가 한동안 재빌드되지 않아 ECR 이미지는 여전히 `app.main:app`을 참조하고 있었음.

```
이미지의 실제 CMD: uvicorn app.main:app  ← 파일 없음
코드의 실제 경로: app/services/main.py
```

### 해결
- `app/services/main.py` → `app/main.py` 로 원복
- Dockerfile CMD를 `app.main:app`으로 복구

---

## 문제 2: ECR push 403 Forbidden

### 증상
```
ERROR: failed to build: failed to solve: failed to push
620758375333.dkr.ecr.ap-northeast-2.amazonaws.com/cdci-prd-analysis-agent:xxxx:
unexpected status from HEAD request to .../manifests/xxxx: 403 Forbidden
```

### 원인
`docker buildx`의 `docker-container` 드라이버가 BuildKit을 별도 컨테이너에서 실행하는데, 이 컨테이너가 호스트의 ECR 로그인 credentials를 상속받지 못함.
`docker/build-push-action@v5`로 교체해도 동일 증상 발생.

### 해결
buildx push를 제거하고 **build → tar 저장 → docker load → docker push** 방식으로 변경.

```yaml
docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  --output type=docker,dest=/tmp/image.tar \
  -t $IMAGE_URI \
  .
docker load -i /tmp/image.tar
docker push $IMAGE_URI
```

---

## 문제 3: RuntimeClientError 500 - Lambda 함수명 불일치

### 증상
```
RuntimeClientError: Received error (500) from runtime.
```

컨테이너 로컬 실행 시:
```
ResourceNotFoundException: Function not found:
arn:aws:lambda:ap-northeast-2:620758375333:function:action-nutrient-calc:$LATEST
```

### 원인
`config.py`의 `LAMBDA_FUNCTION_NAME` 기본값이 `action-nutrient-calc`인데
실제 배포된 Lambda 함수명은 `cdci-prd-nutrient-calc`.

### 해결
AgentCore Runtime 환경변수에 `LAMBDA_FUNCTION_NAME=cdci-prd-nutrient-calc` 추가.

`deploy.yml`:
```yaml
--environment-variables '{"LLM_PROVIDER":"bedrock","BEDROCK_MODEL_ID":"...","LAMBDA_FUNCTION_NAME":"cdci-prd-nutrient-calc"}'
```

---

## 문제 4: 422 - LLM JSON 파싱 실패

### 증상
```
RuntimeClientError: Received error (422) from runtime.
```

컨테이너 로컬 실행 시:
```
{"detail":"LLM 응답 파싱 실패: Expecting value: line 1 column 1 (char 0)"}
```

### 원인
Bedrock Claude가 JSON 앞뒤에 설명 텍스트를 붙여서 응답:
```
죄송합니다. 영양제 목록이 제공되지 않아...

{
  "recommendations": []
}

실제 추천을 위해서는...
```

기존 `_parse_json`은 ` ``` ` 블록만 처리하고 이 케이스를 처리하지 못함.

### 해결
`_parse_json`에서 `{` ~ `}` 범위를 직접 추출하도록 수정.

```python
start = text.find("{")
end = text.rfind("}") + 1
if start != -1 and end > start:
    text = text[start:end]
```

---

## 문제 5: analysis 백엔드 AWS_REGION 불일치

### 증상
로컬에서 analysis 백엔드가 AgentCore를 호출하지 못함.

### 원인
`codecaine-python-analysis/backend/.env`의 `AWS_REGION=us-east-1`로 설정되어 있으나
AgentCore Runtime은 `ap-northeast-2`에 배포됨.

### 해결
`.env`에서 `AWS_REGION=ap-northeast-2`로 수정.

---

## 최종 정상 동작 확인

```
invoke_agent_runtime() 호출
→ step1: 필요 영양소 분석 (LLM)
→ step2: 갭 계산 (Lambda: cdci-prd-nutrient-calc)
→ step3: 영양제 추천 (LLM)
→ 정상 JSON 응답 반환
```
