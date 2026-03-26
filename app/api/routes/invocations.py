import time
from fastapi import APIRouter, HTTPException
from app.schemas.analysis import AnalysisRequest, AnalysisResponse
from app.services.analysis_agent import AnalysisAgent
from app.metrics import agent_invocation_counter, agent_latency_histogram

router = APIRouter()

AGENT_NAME = "analysis-agent"


@router.post("/invocations", response_model=AnalysisResponse)
async def invocations(req: AnalysisRequest):
    """
    AgentCore Runtime 필수 엔드포인트.
    App이 invoke_agent_runtime() 호출 시 이 엔드포인트가 실행됨.
    """
    start = time.time()
    status = "success"
    try:
        agent = AnalysisAgent()
        return await agent.run(req)
    except ValueError as e:
        status = "error"
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        status = "error"
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        agent_invocation_counter.add(1, {"agent_name": AGENT_NAME, "status": status})
        agent_latency_histogram.record(time.time() - start, {"agent_name": AGENT_NAME})