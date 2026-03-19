# invoke_test.py 수정
import boto3, json

client = boto3.client("bedrock-agentcore", region_name="ap-northeast-2")

response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:ap-northeast-2:070238434919:runtime/analysis_agent-bA2YOVDhUj",
    payload=json.dumps({
        "cognito_id": "test-user",
        "intake_purpose": "피로 회복",
        "codef_health_data": {"vitamin_d": 15, "ferritin": 10},
        "current_supplements": [],
        "unit_cache": {"비타민D": "0.000025", "mcg": "0.001"},
        "products": []
    })
)
print("contentType:", response.get("contentType"))

# 스트리밍 응답 처리
raw = b""
for chunk in response.get("response", []):
    raw += chunk

print("raw:", raw.decode("utf-8"))

try:
    result = json.loads(raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))
except:
    print("JSON 파싱 실패, raw 출력 위에 있음")