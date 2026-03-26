import logging
import boto3
from app.core.config import settings

logger = logging.getLogger(__name__)

NAMESPACE = "CDCI/AgentCore"
_cw = None


def _get_client():
    global _cw
    if _cw is None:
        _cw = boto3.client("cloudwatch", region_name=settings.AWS_REGION)
    return _cw


def _put(metric_name: str, value: float, dimensions: dict, unit: str = "Count"):
    try:
        _get_client().put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{
                "MetricName": metric_name,
                "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
                "Value": value,
                "Unit": unit,
            }]
        )
    except Exception as e:
        logger.warning(f"CloudWatch put_metric_data 실패: {e}")


class _Counter:
    def __init__(self, name: str):
        self.name = name

    def add(self, value: float, attributes: dict):
        _put(self.name, value, attributes, "Count")


class _Histogram:
    def __init__(self, name: str):
        self.name = name

    def record(self, value: float, attributes: dict):
        _put(self.name, value, attributes, "Seconds")


def init_metrics():
    """CloudWatch 클라이언트 초기화 (컨테이너 시작 시 1회 호출)"""
    _get_client()


agent_invocation_counter   = _Counter("agent_invocation_total")
agent_token_input_counter  = _Counter("agent_token_input_total")
agent_token_output_counter = _Counter("agent_token_output_total")
agent_latency_histogram    = _Histogram("agent_latency_seconds")
tool_execution_counter     = _Counter("tool_execution_total")
tool_duration_histogram    = _Histogram("tool_execution_duration_seconds")
tool_approval_counter      = _Counter("tool_approval_total")
