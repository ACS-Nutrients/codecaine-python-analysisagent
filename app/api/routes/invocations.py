from fastapi import APIRouter, HTTPException
from app.schemas.analysis import AnalysisRequest, AnalysisResponse
from app.services.analysis_agent import AnalysisAgent

router = APIRouter()


@router.post("/invocations", response_model=AnalysisResponse)
async def invocations(req: AnalysisRequest):
    """
    AgentCore Runtime 필수 엔드포인트.
    App이 invoke_agent_runtime() 호출 시 이 엔드포인트가 실행됨.
    """
    try:
        agent = AnalysisAgent()
        return await agent.run(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))