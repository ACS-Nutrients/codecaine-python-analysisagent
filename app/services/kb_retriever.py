"""
kb_retriever.py

이미지에 포함된 KB 파일에서 영양소-의약품 상호작용 정보를 검색.

KB 파일:
  lpi_kb.npz        — 정규화된 임베딩 벡터 (252, 1536) float32
  lpi_kb_texts.json — 문서 텍스트 + 메타데이터

벡터 검색: numpy cosine similarity (브루트포스)
임베딩 모델: global.cohere.embed-v4:0 (AWS Bedrock, 1536차원)
"""

import json
import logging

import boto3
import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_COHERE_MODEL_ID = "global.cohere.embed-v4:0"


def _embed_query(text: str) -> np.ndarray:
    """Cohere Bedrock으로 쿼리 임베딩 후 정규화된 벡터 반환."""
    client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
    response = client.invoke_model(
        modelId=_COHERE_MODEL_ID,
        body=json.dumps({
            "texts": [text],
            "input_type": "search_query",
            "embedding_types": ["float"],
        }),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    vec = np.array(result["embeddings"]["float"][0], dtype=np.float32)
    return vec / np.linalg.norm(vec)


# 컨테이너 내 캐싱 (요청마다 파일을 다시 읽지 않도록)
_vectors = None   # (252, 1536) float32, 미리 정규화됨
_texts = None     # {"documents": [...], "metadatas": [...]}


def _get_kb():
    global _vectors, _texts
    if _vectors is not None:
        return _vectors, _texts

    vectors_path = f"{settings.KB_LOCAL_PATH}/lpi_kb.npz"
    texts_path = f"{settings.KB_LOCAL_PATH}/lpi_kb_texts.json"

    _vectors = np.load(vectors_path)["vectors"]
    with open(texts_path, encoding="utf-8") as f:
        _texts = json.load(f)

    logger.info(f"[KB] 로드 완료 — {_vectors.shape[0]}개 청크, {_vectors.shape[1]}차원")
    return _vectors, _texts


def retrieve(query: str) -> str:
    """
    쿼리와 관련된 영양소-의약품 상호작용 정보 검색.

    Returns:
        관련 청크들을 합친 텍스트 (프롬프트 주입용)
    """
    try:
        vectors, texts = _get_kb()
        query_vec = _embed_query(query)

        # cosine similarity: 이미 정규화된 벡터끼리 dot product = cosine similarity
        similarities = vectors @ query_vec
        top_indices = np.argsort(similarities)[::-1][:settings.KB_TOP_K]

        docs = [texts["documents"][i] for i in top_indices]
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

    # 약물별 개별 쿼리
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
