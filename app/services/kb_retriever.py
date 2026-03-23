"""
kb_retriever.py

이미지에 포함된 Chroma DB에서 영양소-의약품 상호작용 정보를 검색.

Chroma DB는 빌드 시 이미지에 포함 (lpi_vector_db/ → /app/lpi_vector_db/)
S3 다운로드 불필요, 콜드스타트 없음.
"""

import logging
import chromadb

from app.core.config import settings

logger = logging.getLogger(__name__)

# 컨테이너 내 캐싱 (요청마다 새로 로드하지 않도록)
_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection

    client = chromadb.PersistentClient(path=settings.KB_LOCAL_PATH)

    collections = client.list_collections()
    logger.info(f"[KB] 사용 가능한 collections: {[c.name for c in collections]}")

    _collection = client.get_collection(name=settings.KB_COLLECTION_NAME)
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
    """
    if not medications and not required_nutrients:
        return ""

    med_names = [m.get("name", "") for m in medications if m.get("name")]

    if med_names and required_nutrients:
        query = f"{' '.join(med_names)} {' '.join(required_nutrients)} 상호작용"
    elif med_names:
        query = f"{' '.join(med_names)} 영양소 상호작용"
    elif required_nutrients:
        query = f"{' '.join(required_nutrients)} 의약품 상호작용"
    else:
        return ""

    return retrieve(query)