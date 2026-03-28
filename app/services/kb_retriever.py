"""
kb_retriever.py

이미지에 포함된 Chroma DB에서 영양소-의약품 상호작용 정보를 검색.

Chroma DB는 빌드 시 이미지에 포함 (lpi_vector_db/ → /app/lpi_vector_db/)
S3 다운로드 불필요, 콜드스타트 없음.
"""

import json
import logging
import boto3
import chromadb
from chromadb import EmbeddingFunction, Embeddings

from app.core.config import settings

logger = logging.getLogger(__name__)

_COHERE_MODEL_ID = "global.cohere.embed-v4:0"


class CohereBedrockEmbeddingFn(EmbeddingFunction):
    """
    임베딩 모델 : global.cohere.embed-v4:0 (AWS Bedrock Inference Profile)
    임베딩 차원 : 1536
    유사도 공간 : cosine
    다국어      : 한국어/영어 크로스랭귀얼 지원

    input_type:
      ChromaDB 환경에서는 저장·쿼리 모두 "search_query" 고정.
      search_document/search_query를 혼용하면 서로 다른 벡터 부공간에 임베딩이 생성되어
      코사인 유사도가 음수(-0.87~-0.96)로 계산됨 → 검색 실패.
    """
    def __init__(self, input_type: str = "search_query"):
        self._client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        self._input_type = input_type

    def __call__(self, input: list[str]) -> Embeddings:
        response = self._client.invoke_model(
            modelId=_COHERE_MODEL_ID,
            body=json.dumps({
                "texts": input,
                "input_type": self._input_type,
                "embedding_types": ["float"],
            }),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        return result["embeddings"]["float"]


# 컨테이너 내 캐싱 (요청마다 새로 로드하지 않도록)
_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection

    client = chromadb.PersistentClient(path=settings.KB_LOCAL_PATH)

    collections = client.list_collections()
    logger.info(f"[KB] 사용 가능한 collections: {[c.name for c in collections]}")

    embedding_fn = CohereBedrockEmbeddingFn(input_type="search_query")
    _collection = client.get_collection(
        name=settings.KB_COLLECTION_NAME,
        embedding_function=embedding_fn,
    )
    logger.info(f"[KB] collection 로드 완료 — {_collection.count()}개 청크")

    return _collection


def retrieve(query: str) -> str:
    """
    쿼리와 관련된 영양소-의약품 상호작용 정보 검색.

    Returns:
        관련 청크들을 합친 텍스트 (프롬프트 주입용)
    """
    try:
        collection = _get_collection()
        results = collection.query(
            query_texts=[query],
            n_results=settings.KB_TOP_K,
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            logger.info(f"[KB] 검색 결과 없음: {query}")
            return ""

        context = "\n\n".join(docs)
        logger.info(f"[KB] {len(docs)}개 청크 검색됨 (query: {query[:50]})")
        return context

    except Exception as e:
        logger.warning(f"[KB] 검색 실패: {e} — KB 없이 진행")
        return ""


def retrieve_drug_interactions(
    medications: list[dict],
    required_nutrients: list[str],
) -> str:
    """
    복용 의약품 + 영양소 기반으로 상호작용 정보 검색.
    Step 1 LLM 호출 전 실행하여 프롬프트에 주입.

    약물명별로 개별 쿼리를 날려 관련 청크를 수집한 뒤 합쳐서 반환.
    Cohere embed-v4의 크로스랭귀얼 특성으로 한국어 쿼리도 영어 KB에서 검색 가능.
    """
    if not medications and not required_nutrients:
        return ""

    med_names = [m.get("name", "") for m in medications if m.get("name")]
    contexts = []

    # 약물별 개별 쿼리 (약물명 + "drug nutrient interaction" 패턴)
    for med in med_names:
        context = retrieve(f"{med} drug nutrient vitamin interaction")
        if context:
            contexts.append(context)

    # 영양소와 약물을 조합한 통합 쿼리
    if med_names and required_nutrients:
        context = retrieve(f"{' '.join(med_names)} {' '.join(required_nutrients)} interaction")
        if context:
            contexts.append(context)
    elif required_nutrients:
        context = retrieve(f"{' '.join(required_nutrients)} drug interaction")
        if context:
            contexts.append(context)

    return "\n\n".join(contexts)