# KB 벡터 검색 교체 안내 — ChromaDB → numpy (2026-03-28)

## 변경 배경

GitOps CI/CD 빌드 시간 단축을 위해 ChromaDB를 numpy 기반 검색으로 교체했습니다.

ChromaDB는 내부적으로 `hnswlib`(C++ 컴파일 필요)를 의존성으로 사용해 Docker 빌드 시간이 증가하는 원인이었습니다.
analysis-agent의 KB 규모(252청크)에서는 HNSW 같은 근사 탐색 알고리즘이 불필요하며, numpy dot product로 동일한 검색 품질을 얻을 수 있습니다.

---

## 변경 내용

### 의존성

```diff
# requirements.txt
- chromadb==0.5.23
+ numpy==1.26.4
```

### KB 파일

```diff
# Dockerfile
- COPY lpi_vector_db/ ./lpi_vector_db/   # 10MB (SQLite + HNSW 인덱스)
+ COPY lpi_kb.npz ./lpi_kb.npz           # 0.9MB (정규화된 임베딩 벡터)
+ COPY lpi_kb_texts.json ./lpi_kb_texts.json  # 0.2MB (문서 텍스트 + 메타데이터)
```

| | 이전 (ChromaDB) | 이후 (numpy) |
|---|---|---|
| KB 파일 크기 | 10MB | 1.1MB |
| 추가 빌드 의존성 | hnswlib (C++ 컴파일) | 없음 |
| 검색 방식 | HNSW 근사 탐색 | numpy cosine similarity |

### config.py

```diff
- KB_LOCAL_PATH: str = "/app/lpi_vector_db"
- KB_COLLECTION_NAME: str = "lpi_interactions"   # 삭제
+ KB_LOCAL_PATH: str = "/app"
```

---

## KB 동작 방식

임베딩 모델과 검색 로직은 변경 없습니다. 벡터 저장소만 ChromaDB → numpy 파일로 바뀌었습니다.

```
사용자 약물명 → Cohere Bedrock 임베딩 → numpy cosine similarity → top-k 문서 → LLM 프롬프트 주입
```

### 검색 원리

```python
# lpi_kb.npz: 252개 문서 벡터를 미리 정규화해서 저장
# 쿼리 시: 쿼리 벡터 정규화 후 dot product = cosine similarity
similarities = doc_vectors @ query_vector   # (252,) 유사도 점수
top_k = np.argsort(similarities)[::-1][:3]  # 상위 3개 인덱스
```

---

## KB 재빌드가 필요한 경우

LPI 데이터(`lpi_comprehensive_kb.json`)가 변경된 경우 아래 순서로 재빌드합니다.

**1. ChromaDB로 임베딩 재빌드**

```bash
cd jisu-data-crawling-series/lpi-crawling/nutrient-crawling
python 2_kb_rebuild_cohere.py
# → lpi_vector_db/ 재생성 (Cohere Bedrock, search_query 대칭 방식)
```

**2. numpy 파일로 변환**

```bash
cd analysis-agent-ver1

python - << 'EOF'
import chromadb, json, numpy as np

client = chromadb.PersistentClient(path="../lpi-crawling/nutrient-crawling/lpi_vector_db")
col = client.get_collection("lpi_interactions")
result = col.get(include=["embeddings", "documents", "metadatas"])

vecs = np.array(result["embeddings"], dtype=np.float32)
vecs_normalized = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
np.savez_compressed("lpi_kb.npz", vectors=vecs_normalized)

with open("lpi_kb_texts.json", "w", encoding="utf-8") as f:
    json.dump({
        "documents": result["documents"],
        "metadatas": result["metadatas"],
        "ids": result["ids"],
    }, f, ensure_ascii=False, indent=2)

print(f"완료: {vecs_normalized.shape[0]}개 청크")
EOF
```

**3. 커밋 후 배포**

```bash
git add lpi_kb.npz lpi_kb_texts.json
git commit -m "feat: KB 재빌드"
git push origin main:deploy
```

---

## 테스트 결과

동일한 input으로 ChromaDB 버전 / numpy 버전을 동시에 호출해 비교한 결과, **KB 검색 품질 동일** 확인.

테스트 input: 와파린 + 아토르바스타틴 복용, 비타민D 결핍, 혈압 관리 목적

| 항목 | numpy | ChromaDB |
|---|---|---|
| 비타민 D 추천 | ✓ | ✓ |
| CoQ10 추천 (스타틴 부작용) | ✓ | ✓ |
| 와파린+비타민K 주의 (key_concerns) | ✓ | ✓ |
| 와파린+비타민C 제한 (KB 유래) | ✓ | ✓ |

두 결과의 미세한 차이(추천 영양소 수, rda 수치)는 KB 차이가 아닌 LLM 생성의 자연스러운 변동입니다.

---

## 로컬 개발 시 .env 설정

```env
KB_LOCAL_PATH=.
KB_TOP_K=3
```

> `KB_COLLECTION_NAME`은 더 이상 사용하지 않습니다.
