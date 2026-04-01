"""
AgentCore Runtime 컨트랙트:
  - GET  /ping         → 헬스체크 (AgentCore가 컨테이너 상태 확인)
  - POST /invocations  → Agent 실행 진입점
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.ext.fastapi.middleware import XRayMiddleware
from app.api.routes import invocations

from app.metrics import init_metrics
from app.telemetry import setup_xray
init_metrics()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

setup_xray("cdci-prd-analysis-agent")

app = FastAPI(title="Analysis Agent", version="1.0.0")

app.add_middleware(XRayMiddleware, recorder=xray_recorder)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# AgentCore Runtime 필수 엔드포인트
app.include_router(invocations.router)


@app.get("/ping")
async def ping():
    """AgentCore Runtime 헬스체크"""
    return {"status": "ok"}