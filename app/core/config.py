from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    AWS_REGION: str = "ap-northeast-2"

    # AgentCore Runtime은 IAM Role로 자동 인증
    # 로컬 테스트 시에만 설정
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None

    # LLM 제공자 선택: "bedrock" / "anthropic" / "openai"
    LLM_PROVIDER: str = "openai"

    # Bedrock 사용 시
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"

    # Anthropic API 직접 사용 시
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_ID: str = "claude-sonnet-4-5"

    # OpenAI API 사용 시
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL_ID: str = "gpt-4o"

    # Lambda 함수명 (변경 시 .env에서만 수정)
    LAMBDA_FUNCTION_NAME: str = "action-nutrient-calc"

    class Config:
        env_file = ".env"


settings = Settings()