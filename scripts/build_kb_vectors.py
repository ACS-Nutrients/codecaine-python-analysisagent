"""
한국어 KB 벡터 재생성 스크립트
- 입력: lpi_kb_texts_ko.json
- 출력: lpi_kb.npz (기존 파일 덮어쓰기)

임베딩 모델 : global.cohere.embed-v4:0 (AWS Bedrock Inference Profile)
임베딩 차원 : 1536
input_type  : "search_query" 고정 (저장·쿼리 모두 동일해야 코사인 유사도 정상 작동)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3
import numpy as np

REGION = "ap-northeast-2"
MODEL_ID = "global.cohere.embed-v4:0"
BATCH_SIZE = 96  # Cohere embed-v4 최대 배치

INPUT_PATH = Path("lpi_kb_texts_ko.json")
OUTPUT_PATH = Path("lpi_kb_ko.npz")
CHECKPOINT_PATH = Path("scripts/build_kb_vectors_checkpoint.json")


def embed_batch(texts: list[str], client) -> list[list[float]]:
    response = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "texts": texts,
            "input_type": "search_query",
            "embedding_types": ["float"],
        }),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return result["embeddings"]["float"]


def load_checkpoint() -> dict[int, list[float]]:
    if CHECKPOINT_PATH.exists():
        return {int(k): v for k, v in json.loads(CHECKPOINT_PATH.read_text()).items()}
    return {}


def save_checkpoint(done: dict[int, list[float]]):
    CHECKPOINT_PATH.write_text(json.dumps(done, ensure_ascii=False))


def main():
    client = boto3.client("bedrock-runtime", region_name=REGION)

    kb = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    docs = kb["documents"]
    total = len(docs)
    print(f"총 {total}개 문서 임베딩 시작")

    done: dict[int, list[float]] = load_checkpoint()
    if done:
        print(f"체크포인트 재개: {len(done)}개 완료")

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_indices = list(range(batch_start, batch_end))

        if all(i in done for i in batch_indices):
            print(f"[{batch_start+1:3d}-{batch_end:3d}] 스킵")
            continue

        batch_texts = [docs[i] for i in batch_indices]
        print(f"[{batch_start+1:3d}-{batch_end:3d}] 임베딩 중...", end=" ", flush=True)

        try:
            vecs = embed_batch(batch_texts, client)
            for i, v in zip(batch_indices, vecs):
                done[i] = v
            save_checkpoint(done)
            print(f"완료 ({len(done)}/{total})")
        except Exception as e:
            print(f"실패: {e}")
            raise

        time.sleep(0.2)

    # 정규화 후 저장
    vectors = np.array([done[i] for i in range(total)], dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / norms

    np.savez_compressed(OUTPUT_PATH, vectors=vectors)
    print(f"\n저장 완료: {OUTPUT_PATH}  shape={vectors.shape}")
    print(f"norm 범위: {np.linalg.norm(vectors, axis=1).min():.6f} ~ {np.linalg.norm(vectors, axis=1).max():.6f}")
    CHECKPOINT_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
