# AgentCore Runtime은 arm64 아키텍처 필수
FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8080

# AgentCore Runtime 기본 포트 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]