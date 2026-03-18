import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# 로컬 실행 시 .env 로드 (Lambda에서는 환경변수로 주입되므로 무시됨)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://vitamin_analysis:vitamin_analysis123!@13.125.230.157:5432/vitamin_analysis",
)


def get_conn():
    """PostgreSQL 연결 반환 (Lambda invocation당 1회 사용 후 close)"""
    return psycopg2.connect(DATABASE_URL)


def cursor(conn):
    """RealDictCursor 반환 — 컬럼명 키로 결과 접근"""
    return conn.cursor(cursor_factory=RealDictCursor)
