import json
import logging
import time
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.schemas.analysis import AnalysisRequest
from app.services.analysis_agent import AnalysisAgent
from app.metrics import agent_invocation_counter, agent_latency_histogram

router = APIRouter()
logger = logging.getLogger(__name__)

AGENT_NAME = "analysis-agent"


@router.post("/invocations")
async def invocations(request: Request):
    raw = await request.body()
    logger.info(f"[INVOCATIONS] raw body: {raw[:300]}")

    start = time.time()
    status = "success"

    try:
        data = json.loads(raw)
        req = AnalysisRequest(**data)
    except Exception as e:
        logger.error(f"[INVOCATIONS] Request parse failed: {e} | raw={raw[:300]}")
        return JSONResponse(status_code=422, content={"error": f"Request parse error: {e}"})

    try:
        agent = AnalysisAgent()
        result = await agent.run(req)
        return JSONResponse(content=result.model_dump(mode="json"))
    except ValueError as e:
        status = "error"
        logger.error(f"[INVOCATIONS] Validation error: {e}")
        return JSONResponse(status_code=422, content={"error": str(e)})
    except Exception as e:
        status = "error"
        logger.error(f"[INVOCATIONS] Agent run failed: {traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {str(e)}"})
    finally:
        agent_invocation_counter.add(1, {"agent_name": AGENT_NAME, "status": status})
        agent_latency_histogram.record(time.time() - start, {"agent_name": AGENT_NAME})