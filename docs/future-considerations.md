# 추후 고려사항

---

## ChromaDB → numpy 교체 (KB 벡터 검색)

### 배경

현재 `kb_retriever.py`는 ChromaDB를 사용해 `lpi_vector_db/`에서 벡터 검색을 수행.
ChromaDB는 내부적으로 hnswlib(C++ 빌드)를 의존성으로 끌어와 **GitOps CI/CD 빌드 시간이 증가**하는 원인.

### 문제

`chromadb==0.5.23`이 요구하는 hnswlib는 C++ 컴파일이 필요한 패키지로,
Docker 빌드 시 컴파일 또는 플랫폼 전용 wheel 다운로드에 시간이 소요됨.

### 제안

ChromaDB를 제거하고 **numpy 기반 cosine similarity**로 교체.

```
현재: query → ChromaDB (HNSW 근사 탐색) → top-k
변경: query → numpy dot product (브루트포스) → top-k
```

**252청크 기준 성능 비교**

| 항목 | ChromaDB | numpy |
|---|---|---|
| 검색 방식 | HNSW 근사 최근접 이웃 | 전체 비교 (브루트포스) |
| 검색 속도 | ~1ms | ~0.1ms (오히려 빠름) |
| 메모리 | ~10MB (SQLite + HNSW 인덱스) | ~1.5MB (.npz 파일) |
| 추가 의존성 | hnswlib (C++ 빌드) | numpy (이미 포함) |

HNSW는 수만~수백만 개 벡터 탐색에서 효율적인 알고리즘으로, 252청크 수준에는 과도한 선택.

### 전환 시 작업 범위

1. KB 재빌드 스크립트 → `.npz`(벡터) + `.json`(텍스트·메타데이터) 저장으로 변경
2. `kb_retriever.py` → numpy cosine similarity로 교체
3. `requirements.txt`에서 `chromadb` 제거

### 전환 기준

- KB 청크 수가 수천 개 이상으로 증가하면 HNSW가 다시 유리해질 수 있음
- 그 전까지는 numpy 방식이 단순성·빌드 속도 면에서 우세

---
