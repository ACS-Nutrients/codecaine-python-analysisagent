import os
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

def init_metrics():
    """ADOT 사이드카로 메트릭 전송 초기화 (컨테이너 시작 시 1회 호출)"""
    exporter = OTLPMetricExporter(
        endpoint="http://localhost:4317",  # ADOT 사이드카 (변경 불필요)
        insecure=True
    )
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=30000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)

meter = metrics.get_meter("cdci.agentcore", version="1.0")

# 메트릭 객체 (모듈 레벨에서 한 번만 생성)
agent_invocation_counter = meter.create_counter(
    "agent_invocation_total",
    description="에이전트 호출 횟수"
)
agent_token_input_counter = meter.create_counter(
    "agent_token_input_total",
    description="입력 토큰 수"
)
agent_token_output_counter = meter.create_counter(
    "agent_token_output_total",
    description="출력 토큰 수"
)
agent_latency_histogram = meter.create_histogram(
    "agent_latency_seconds",
    description="에이전트 응답 시간 (초)"
)
tool_execution_counter = meter.create_counter(
    "tool_execution_total",
    description="Tool 실행 횟수"
)
tool_duration_histogram = meter.create_histogram(
    "tool_execution_duration_seconds",
    description="Tool 실행 시간 (초)"
)
tool_approval_counter = meter.create_counter(
    "tool_approval_total",
    description="Tool 승인/거절 횟수"
)