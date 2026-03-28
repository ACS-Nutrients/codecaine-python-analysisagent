# Knowledge Base (KB) 정리 — 2026-03-27

## 임베딩이란?

텍스트를 숫자 벡터로 변환하는 기술. 의미가 유사한 텍스트는 벡터 공간에서 가까운 위치에 배치됨.

```
"와파린 상호작용"      →  [0.12, -0.34, 0.91, ...]  ← 1536개 숫자
"Warfarin interaction" →  [0.11, -0.33, 0.89, ...]  ← 비슷한 위치 (크로스랭귀얼)
```

이 성질을 이용해 한국어 쿼리로 영어 문서를 검색하는 것이 가능함.

---

## 임베딩 타입

| 타입 | 설명 | 사용 케이스 |
|---|---|---|
| Dense Vector (밀집 벡터) | 모든 차원에 값이 있는 벡터 | 의미 기반 검색 (RAG) ← **현재 사용** |
| Sparse Vector (희소 벡터) | 대부분 0, 키워드 기반 (BM25 등) | 키워드 정확 매칭 |
| Hybrid | Dense + Sparse 혼합 | 정밀도가 중요한 검색 |

현재 analysis-agent는 **Dense Vector** 방식 사용.

---

## 개요

analysis-agent가 영양소-의약품 상호작용 정보를 LLM에 주입하는 RAG 파이프라인.
LPI(Linus Pauling Institute) 영어 데이터를 ChromaDB에 저장 후, 사용자의 복용 약물명으로 검색해 Step1 LLM 프롬프트에 삽입.

```
사용자 약물 목록 → kb_retriever.retrieve() → ChromaDB 검색 → Step1 LLM 프롬프트에 주입
```

---

## KB 데이터

| 항목 | 값 |
|---|---|
| 원본 데이터 | `lpi_comprehensive_kb.json` (LPI 크롤링, 영어) |
| 저장 경로 | `lpi_vector_db/` (ChromaDB PersistentClient) |
| 컬렉션 이름 | `lpi_interactions` |
| 총 청크 수 | 252개 |
| 청킹 전략 | RecursiveCharacterTextSplitter (chunk_size=800, overlap=150) |
| 빌드 스크립트 | `jisu-data-crawling-series/lpi-crawling/nutrient-crawling/2_kb_rebuild_cohere.py` |

### lpi_vector_db/ 디렉토리 구조

```
lpi_vector_db/
  chroma.sqlite3                         ← 컬렉션 메타데이터 + 문서 텍스트 (SQLite)
  bc99f2ce-5e8d-482f-8f2d-fec04d645da5/  ← HNSW 벡터 인덱스 (현재 컬렉션)
    data_level0.bin                      ← 벡터 데이터 (~6MB)
    header.bin
    length.bin
    link_lists.bin
```

두 파일이 한 세트로 동작. 하나라도 없으면 ChromaDB 로드 실패.

---

## 임베딩 모델

```
모델 ID  : global.cohere.embed-v4:0  (AWS Bedrock Inference Profile)
모델명   : Cohere Embed v4
차원     : 1536
유사도   : cosine (ChromaDB hnsw:space=cosine)
다국어   : 100개 이상 언어 지원 — 한국어 쿼리로 영어 문서 검색 가능 (크로스랭귀얼)
비용     : $0.00010 / 1K 토큰
```

### input_type 설정 — 핵심 주의사항

Cohere Embed v4는 `search_document`/`search_query` 두 가지 input_type을 제공하지만,
**ChromaDB 환경에서는 저장·쿼리 모두 동일한 `search_query`를 사용해야 함.**

| 방식 | 저장 | 쿼리 | 결과 |
|---|---|---|---|
| Cohere 공식 권장 | `search_document` | `search_query` | 코사인 유사도 음수(-0.87~-0.96) → 검색 실패 |
| **현재 적용** | **`search_query`** | **`search_query`** | **정상 검색** |

> **이유**: 두 input_type은 서로 다른 벡터 부공간(subspace)에 임베딩을 생성함.
> ChromaDB가 두 공간 사이의 코사인 유사도를 계산하면 항상 음수가 나와 관련 문서를 찾지 못함.
> 동일한 input_type을 쓰면 같은 공간에 위치하므로 정상 동작.

---

## 임베딩 함수 코드

다른 서비스에서 동일 KB를 사용할 때 이 클래스를 그대로 복사해서 쓸 것.

```python
import json
import boto3
from chromadb import EmbeddingFunction, Embeddings

_COHERE_MODEL_ID = "global.cohere.embed-v4:0"

class CohereBedrockEmbeddingFn(EmbeddingFunction):
    """
    임베딩 모델 : global.cohere.embed-v4:0 (AWS Bedrock Inference Profile)
    임베딩 차원 : 1536
    유사도 공간 : cosine
    다국어      : 한국어/영어 크로스랭귀얼 지원

    ChromaDB 사용 시: 저장·쿼리 모두 input_type="search_query" 고정
    """
    def __init__(self, region: str = "ap-northeast-2", input_type: str = "search_query"):
        self._client = boto3.client("bedrock-runtime", region_name=region)
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
```

> `from chromadb import EmbeddingFunction, Embeddings`는 타입 정의용 import.
> 실제 임베딩 연산은 boto3만 사용하므로, 추후 ChromaDB를 제거해도 이 클래스 자체는 boto3 의존성만으로 동작 가능.

---

## KB 사용 방법

### 전제 조건

- AWS credentials에 Bedrock 접근 권한 필요 (`bedrock-runtime` invoke)
- `lpi_vector_db/` 디렉토리가 접근 가능한 경로에 있어야 함
  - 로컬: `./lpi_vector_db` (`.env`의 `KB_LOCAL_PATH`)
  - 컨테이너: `/app/lpi_vector_db` (Dockerfile에서 COPY)

### analysis-agent에서 사용하는 방법 (이미 구현됨)

`app/services/kb_retriever.py`의 `retrieve_drug_interactions()` 호출:

```python
from app.services.kb_retriever import retrieve_drug_interactions

# medications: [{"name": "와파린", "dose": "5mg", ...}, ...]
# required_nutrients: ["비타민 D", "마그네슘", ...]
context = retrieve_drug_interactions(medications, required_nutrients)

# context를 Step1 LLM 프롬프트에 주입
if context:
    prompt += f"\n\n[의약품-영양소 상호작용 참고 정보]\n{context}"
```

### 다른 서비스에서 직접 연결하는 방법

```python
import chromadb

# 1. ChromaDB 클라이언트
client = chromadb.PersistentClient(path="/app/lpi_vector_db")

# 2. 임베딩 함수 (반드시 명시적으로 전달)
embedding_fn = CohereBedrockEmbeddingFn(input_type="search_query")

# 3. 컬렉션 로드
collection = client.get_collection(
    name="lpi_interactions",
    embedding_function=embedding_fn,   # ← 없으면 chromadb 기본값(384차원)으로 차원 불일치
)

# 4. 쿼리 (한국어 가능)
results = collection.query(
    query_texts=["와파린 영양소 상호작용"],
    n_results=3,
)
docs = results["documents"][0]
context = "\n\n".join(docs)
```

### KB 재빌드가 필요한 경우

데이터 추가/수정 시:

```bash
cd jisu-data-crawling-series/lpi-crawling/nutrient-crawling
python 2_kb_rebuild_cohere.py   # lpi_vector_db/ 재생성

# agent 프로젝트에 복사
rm -rf analysis-agent-ver1/lpi_vector_db
cp -r ./lpi_vector_db analysis-agent-ver1/lpi_vector_db
```

---

## 모델 선택 비교

| 모델 | 차원 | 한국어 | 비용/1K 토큰 | 비고 |
|---|---|---|---|---|
| **global.cohere.embed-v4:0** | 1536 | 우수 | $0.00010 | **현재 사용** |
| amazon.titan-embed-text-v2:0 | 1024 | 보통 | $0.00002 | AWS 자체, 크로스랭귀얼 약함 |
| text-embedding-3-small (OpenAI) | 1536 | 우수 | $0.00002 | chromadb 내장 함수가 openai SDK v2와 호환 안 됨 |

---

## 오늘 작업 이력 (2026-03-27)

### 발생한 문제들

1. **차원 불일치 (384 vs 1536)**
   - 원인: KB는 OpenAI text-embedding-3-small(1536차원)로 빌드했는데, retriever가 chromadb 기본 embedding function(sentence-transformers, 384차원) 사용
   - 해결: retriever에 `CohereBedrockEmbeddingFn` 명시적으로 전달

2. **chromadb 내장 OpenAIEmbeddingFunction 호환 오류**
   - 원인: openai SDK v2에서 `APIRemovedInV1` 오류
   - 해결: `CohereBedrockEmbeddingFn` 커스텀 클래스로 교체

3. **한국어 쿼리로 영어 KB 검색 불가**
   - 원인: 기존 모델이 크로스랭귀얼 미지원
   - 해결: Cohere Embed v4로 교체 (한국어-영어 크로스랭귀얼 지원)

4. **음수 코사인 유사도 (-0.87 ~ -0.96)**
   - 원인: 저장 시 `input_type="search_document"`, 쿼리 시 `input_type="search_query"` 혼용 → 서로 다른 벡터 부공간
   - 해결: 저장·쿼리 모두 `input_type="search_query"`로 통일 후 재빌드

5. **Pydantic 422 — rda_amount=None**
   - 원인: KB 컨텍스트로 약물 상호작용 정보를 받은 LLM이 "비타민K 주의" 같은 항목을 rda_amount 없이 required_nutrients에 포함
   - 해결: system prompt에 required_nutrients 작성 규칙 추가 + null 필터링 코드 추가

### 변경된 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/kb_retriever.py` | `_OpenAIEmbeddingFn` → `CohereBedrockEmbeddingFn` |
| `app/services/analysis_agent.py` | required_nutrients null 필터링, system prompt 규칙 추가 |
| `lpi_vector_db/` | Cohere 임베딩으로 재빌드 (search_query 대칭, 252청크) |
| `jisu-data-crawling-series/.../2_kb_rebuild_cohere.py` | KB 재빌드 스크립트 신규 생성 |

---

## 작업 이력 (2026-03-28)

- 위 변경사항 `deploy` 브랜치 push → GitHub Actions로 ECR 빌드 및 AgentCore 배포 완료
- `.gitignore` 정리: 개별 파일 나열 방식 → `__pycache__/`, `*.pyc` 패턴으로 교체

### 추후 고려사항

현재 ChromaDB는 hnswlib(C++ 빌드) 의존성으로 인해 CI/CD 빌드 시간이 증가함.
252청크 규모에서는 numpy cosine similarity로 교체해도 성능 차이 없음.
→ `docs/future-considerations.md` 참고

