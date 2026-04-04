# Codecaine Python Analysis Agent - 코드베이스 분석 보고서

## 1. 에이전트 개요

**이름**: Analysis Agent  
**역할**: 사용자의 건강 데이터 및 약물 정보를 기반으로 필요 영양소를 분석하고 영양제를 추천하는 멀티 단계 AI 에이전트  
**배포 환경**: AWS Bedrock AgentCore Runtime  
**LLM 제공자**: AWS Bedrock (운영) / Anthropic API / OpenAI API (테스트)

---

## 2. 에이전트 입력 형식 (Input)

### 엔드포인트
```
POST /invocations
```

### 요청 Payload 형식 (AnalysisRequest)

```python
class AnalysisRequest(BaseModel):
    cognito_id: str                          # 필수: 사용자 Cognito ID
    intake_purpose: str | None               # 선택: 섭취 목적 (일반 분석용) - 예: "피로 회복"
    new_purpose: str | None                  # 선택: 새 섭취 목적 (챗봇 재분석용)
    codef_health_data: dict | None           # 선택: CODEF 건강검진 JSON (혈액검사 수치)
    medication_info: list[dict] | None       # 선택: 복용 중인 의약품 정보
    current_supplements: list[dict] | None   # 선택: 현재 복용 영양제
    unit_cache: dict | None                  # 선택: 단위 변환 테이블
    products: list[dict] | None              # 선택: 추천 가능 영양제 목록
    user_profile: dict | None                # 선택: 사용자 프로필 (나이, 성별, 키, 몸무게, 알레르기, 만성질환)
    chat_history: list[dict] | None          # 선택: 챗봇 대화 내역 (재분석 시)
    previous_analysis: dict | None           # 선택: 이전 분석 결과 (재분석 시 맥락 유지)
```

### 입력 예시

```json
{
  "cognito_id": "user-12345",
  "intake_purpose": "피로 회복",
  "codef_health_data": {
    "vitamin_d": 15,
    "ferritin": 10,
    "hemoglobin": 11.5
  },
  "medication_info": [
    {"name": "와파린", "dosage": "5mg", "frequency": "1일 1회"}
  ],
  "current_supplements": [
    {
      "product_id": 1,
      "product_name": "멀티비타민",
      "serving_per_day": 1,
      "ingredients": [
        {"name": "비타민 D", "amount": 400}
      ]
    }
  ],
  "unit_cache": {
    "mcg": "0.001",
    "비타민D": "0.000025",
    "비타민A": "0.000030"
  },
  "user_profile": {
    "gender": "F",
    "birth_dt": "1990-01-01",
    "height": 165,
    "weight": 55,
    "allergies": [],
    "chron_diseases": []
  },
  "products": [
    {
      "product_id": 10,
      "product_name": "프리미엄 비타민D",
      "product_brand": "브랜드A",
      "nutrients": [
        {"nutrient_name": "비타민 D", "amount": 1000, "unit": "IU"}
      ]
    }
  ]
}
```

---

## 3. 에이전트 출력 형식 (Output)

### 응답 Payload 형식 (AnalysisResponse)

```python
class AnalysisResponse(BaseModel):
    cognito_id: str
    step1: Step1Result
    step2: Step2Result
    step3: Step3Result
```

### 상세 응답 구조

#### Step 1: 필요 영양소 분석
```python
class Step1Result:
    required_nutrients: list[RequiredNutrient]
    summary: AnalysisSummary

class RequiredNutrient:
    name_ko: str                   # 영양소명 (한글) - 예: "비타민 D"
    name_en: str | None            # 영양소명 (영문) - 예: "Vitamin D"
    rda_amount: Decimal            # 권장 섭취량 수치
    unit: str                      # 단위 - 예: "IU", "mg", "mcg"
    reason: str                    # 추천 이유

class AnalysisSummary:
    overall_assessment: str        # 전반적 영양 상태 평가 (7문장 이상)
    key_concerns: list[str]        # 주의할 영양소/약물 상호작용
    lifestyle_notes: LifestyleNotes
    risk_warnings: list[str]       # 위험 경고

class LifestyleNotes:
    diet: str                      # 식이 조언 (식품 예시 포함)
    exercise: str                  # 운동 조언
    sleep: str                     # 수면 조언
    supplement_timing: str         # 영양제 복용 타이밍
```

#### Step 2: 영양소 갭 계산
```python
class Step2Result:
    gaps: list[NutrientGapItem]

class NutrientGapItem:
    nutrient_id: int | None        # 영양소 ID (DB)
    name_ko: str                   # 영양소명 (한글)
    name_en: str | None            # 영양소명 (영문)
    unit: str                      # 단위 (항상 "mg"으로 정규화)
    current_amount: str            # 현재 섭취량 (mg)
    gap_amount: str                # 부족한 양 (mg)
    rda_amount: str                # 권장량 (mg)
```

#### Step 3: 영양제 추천
```python
class Step3Result:
    recommendations: list[RecommendationItem]

class RecommendationItem:
    rank: int                      # 순위 (1~5)
    product_id: int                # 제품 ID (DB)
    product_name: str              # 제품명
    product_brand: str             # 브랜드명
    recommend_serving: int         # 권장 복용량 (회/일)
    serving_per_day: int | None    # 일일 복용 횟수
    covered_nutrients: list[str]   # 커버하는 영양소 목록
```

### 출력 예시

```json
{
  "cognito_id": "user-12345",
  "step1": {
    "required_nutrients": [
      {
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "rda_amount": 800,
        "unit": "IU",
        "reason": "검진 결과 결핍 위험"
      },
      {
        "name_ko": "철분",
        "name_en": "Iron",
        "rda_amount": 18,
        "unit": "mg",
        "reason": "페리틴 수치 경계값"
      }
    ],
    "summary": {
      "overall_assessment": "최근 에너지가 많이 떨어지셨을 것 같아요. 비타민D 수치가 18.0 ng/mL로 정상 범위(30~100)에 비해 많이 낮은 편이고, 페리틴도 20 ng/mL로 경계값에 걸쳐 있어 피로감에 영향을 주고 있을 수 있어요. 지금부터 함께 채워나가면 훨씬 가벼워지실 거예요.",
      "key_concerns": [
        "혈색소 수치 경계값 — 철분 결핍 가능성 모니터링 필요",
        "와파린(Warfarin) 복용 중 비타민 K 과다 섭취 주의 — 항응고 효과 감소 위험"
      ],
      "lifestyle_notes": {
        "diet": "등 푸른 생선 주 2회, 달걀노른자 매일 섭취 권장",
        "exercise": "야외 유산소 운동 주 3회, 회당 30분 이상으로 비타민D 합성 촉진",
        "sleep": "마그네슘 섭취 시 취침 1시간 전 복용 시 수면 질 개선에 도움",
        "supplement_timing": "지용성 비타민(A·D·E·K)은 식후, 철분은 공복 복용"
      },
      "risk_warnings": ["⚠️ 와파린 복용 중 비타민K 함유 영양제 주의"]
    }
  },
  "step2": {
    "gaps": [
      {
        "nutrient_id": 1,
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "unit": "mg",
        "current_amount": "10",
        "gap_amount": "15",
        "rda_amount": "25"
      },
      {
        "nutrient_id": 2,
        "name_ko": "철분",
        "name_en": "Iron",
        "unit": "mg",
        "current_amount": "5",
        "gap_amount": "13",
        "rda_amount": "18"
      }
    ]
  },
  "step3": {
    "recommendations": [
      {
        "rank": 1,
        "product_id": 101,
        "product_name": "프리미엄 비타민D + 철분",
        "product_brand": "건강한삶",
        "recommend_serving": 2,
        "serving_per_day": 1,
        "covered_nutrients": ["비타민 D", "철분"]
      },
      {
        "rank": 2,
        "product_id": 102,
        "product_name": "여성용 종합영양제",
        "product_brand": "에너지",
        "recommend_serving": 1,
        "serving_per_day": 1,
        "covered_nutrients": ["비타민 D", "철분", "비타민 B"]
      }
    ]
  }
}
```

---

## 4. 에이전트 내부 구조 (멀티 에이전트 아키텍처)

### 3단계 분석 파이프라인

```
입력 (AnalysisRequest)
    ↓
[Step 1] LLM 분석 — 필요 영양소 도출
    ├─ 지식 베이스 검색: 의약품 상호작용 정보 조회
    ├─ LLM 호출: Step 1 시스템 프롬프트 + 사용자 건강 데이터 입력
    └─ 출력: required_nutrients[], summary (평가, 우려사항, 라이프스타일 조언, 위험 경고)
    ↓
[Step 2] Lambda 함수 — 영양소 갭 계산
    ├─ 입력: required_nutrients, current_supplements, unit_cache
    ├─ 단위 변환 (IU→mg, mcg→mg)
    ├─ 현재 섭취량 집계
    └─ 출력: gaps[] (부족한 영양소 목록)
    ↓
[Step 3] LLM 추천 — 영양제 선택
    ├─ 입력: gaps[], products[] (DB에서 조회한 실제 제품 목록)
    ├─ LLM 호출: Step 3 시스템 프롬프트 + 갭 + 제품 정보
    └─ 출력: recommendations[] (최대 5개, 순위 포함)
    ↓
최종 응답 (AnalysisResponse)
```

### Step 1: LLM 분석 에이전트

**파일**: `/app/services/analysis_agent.py` → `AnalysisAgent.run()` 메서드 내 Step 1 섹션

**동작**:
1. **KB 검색**: `retrieve_drug_interactions()` 호출
   - 복용 의약품 목록에서 약물명 추출
   - Cohere Bedrock Embedding으로 쿼리 임베딩 생성
   - 누적된 임베딩 벡터 DB (lpi_kb.npz)에서 Top-K 청크 검색
   - 의약품-영양소 상호작용 정보를 프롬프트에 주입

2. **프롬프트 구성**: `_build_step1_prompt()` 메서드
   - 섭취 목적, 사용자 프로필, 건강검진 데이터, 복용 의약품, 현재 영양제 정보 포함
   - 챗봇 재분석 시: 이전 분석 결과, 대화 내역 포함

3. **LLM 호출**: `_call_llm()` 메서드
   - LLM_PROVIDER 환경변수에 따라 Bedrock/Anthropic/OpenAI 선택
   - SYSTEM_PROMPT_STEP1 사용 (상세한 영양소 분석 지침 포함)
   - JSON 응답 강제 (response_format 설정 또는 프롬프트 지시)

4. **출력 파싱**: `_parse_json()` 메서드
   - JSON 블록 추출 및 파싱
   - required_nutrients[], summary 생성

**핵심 프롬프트 (SYSTEM_PROMPT_STEP1)**:
```
- 건강검진 수치 분석 (저, 고, 경계값)
- 의약품-영양소 상호작용 검토
  (예: 와파린+비타민K, 스타틴+CoQ10)
- KDRI(한국 영양소 기준 섭취량) 기반 권장량
- 재분석 시: new_purpose 또는 previous_analysis에서 목적 파악
- key_concerns 작성 규칙: 약물명 + 영양소 + 구체적 주의 내용 (뭉뚱그린 표현 금지)
- overall_assessment: 7문장 이상, 공감 어조, 검진 수치 구체적 언급
- lifestyle_notes: 식품 예시, 운동 빈도, 수면과 영양소 연관성, 복용 타이밍 포함
```

---

### Step 2: Lambda 갭 계산 에이전트

**파일**: `/lambdas/action_nutrient_calc/handler.py` → `lambda_handler()` 함수

**동작**:
1. **입력 수신**: 
   - required_nutrients (Step 1 출력)
   - current_supplements (현재 복용 영양제)
   - unit_cache (단위 변환 테이블)

2. **섭취량 집계**: `build_intake_map()` 함수
   - current_supplements에서 각 영양소별 일일 총 섭취량 계산
   - serving_per_day × amount 계산

3. **단위 변환**: `to_mg()` 함수
   ```python
   mg        → 그대로
   mcg/µg/μg → × 0.001 (unit_cache에서 'mcg' factor 조회)
   IU        → × nutrient_name_ko별 factor
              (예: 비타민D → 0.000025, 비타민A → 0.000030)
   ```
   - **중요**: IU 변환 시 영양소 이름으로 factor 조회 (약물명 아님)

4. **갭 계산**: 각 required_nutrient에 대해
   ```
   current_mg = to_mg(현재 섭취량, unit)
   rda_mg     = to_mg(권장량, unit)
   gap_mg     = max(0, rda_mg - current_mg)
   ```

5. **출력**: gaps[] (모든 amount를 mg으로 정규화)
   ```python
   {
     "nutrient_id": int,
     "name_ko": str,
     "name_en": str | None,
     "unit": "mg",  # 항상 mg으로 정규화
     "current_amount": str,
     "gap_amount": str,
     "rda_amount": str
   }
   ```

---

### Step 3: LLM 추천 에이전트

**파일**: `/app/services/analysis_agent.py` → `AnalysisAgent.run()` 메서드 내 Step 3 섹션

**동작**:
1. **프롬프트 구성**: `_build_step3_prompt()` 메서드
   - gaps[] (Step 2 출력) 포함
   - products[] (App이 DB에서 조회한 실제 영양제 제품 목록) 포함
   - gap_amount > 0인 갭만 포함 (효율성)

2. **LLM 호출**: `_call_llm()` 메서드
   - SYSTEM_PROMPT_STEP3 사용
   - Step 1과 동일한 LLM 제공자 사용

3. **추천 기준** (SYSTEM_PROMPT_STEP3):
   ```
   - 제공된 제품 목록에서만 추천 (임의 생성 금지)
   - 부족한 영양소 커버율 높은 제품 우선
   - 성별별 제품 우선순위:
     * 여성: 여성용 영양제 우선, 남성용 제외
     * 남성: 남성용 영양제 우선, 여성용 제외
   - serving_per_day 낮을수록 우선 (복용 편의성)
   - max_amount 초과 위험 제품 하위 순위
   - 최대 5개 추천
   ```

4. **출력**: recommendations[] 리스트

---

## 5. 분석 데이터 소스

### 1) 건강검진 데이터 (CODEF)
- **입력 필드**: `codef_health_data` (dict)
- **내용**: 혈액검사 수치
  ```json
  {
    "vitamin_d": 15,
    "ferritin": 10,
    "hemoglobin": 11.5,
    "...": "기타 검진 수치"
  }
  ```
- **사용처**: Step 1 LLM 프롬프트에 포함 → 필요 영양소 도출

### 2) 의약품 정보 (Medication Info)
- **입력 필드**: `medication_info` (list[dict])
- **구조**:
  ```json
  [
    {"name": "약물명", "dosage": "용량", "frequency": "복용 빈도"},
    ...
  ]
  ```
- **사용처**:
  - Step 1: KB 검색 쿼리로 사용 → 의약품-영양소 상호작용 정보 조회
  - Step 1: 프롬프트에 포함 → key_concerns 생성

### 3) 현재 복용 영양제 (Current Supplements)
- **입력 필드**: `current_supplements` (list[dict])
- **구조**:
  ```json
  [
    {
      "product_id": 1,
      "product_name": "제품명",
      "serving_per_day": 1,
      "ingredients": [
        {"name": "영양소명", "amount": 400},
        ...
      ]
    },
    ...
  ]
  ```
- **사용처**:
  - Step 2: 현재 섭취량 계산 (갭 계산의 기준선)
  - Step 1: 프롬프트에 포함 (컨텍스트)

### 4) 단위 변환 테이블 (Unit Cache)
- **입력 필드**: `unit_cache` (dict)
- **구조**: App이 DB `unit_convertor` 테이블 전체 조회 후 전달
  ```json
  {
    "mcg": "0.001",
    "비타민D": "0.000025",
    "비타민A": "0.000030",
    "비타민E": "0.00067",
    ...
  }
  ```
- **사용처**: Step 2 Lambda에서 IU/mcg → mg 변환

### 5) 영양제 제품 목록 (Products)
- **입력 필드**: `products` (list[dict])
- **구조**: App이 DB `products` + `product_nutrients` 조회 후 전달
  ```json
  [
    {
      "product_id": 10,
      "product_name": "제품명",
      "product_brand": "브랜드",
      "category": "여성용|남성용|일반",
      "nutrients": [
        {"nutrient_name": "비타민 D", "amount": 1000, "unit": "IU"},
        ...
      ]
    },
    ...
  ]
  ```
- **사용처**: Step 3 LLM 프롬프트 → 실제 제품 기반 추천

### 6) 사용자 프로필 (User Profile)
- **입력 필드**: `user_profile` (dict)
- **구조**:
  ```json
  {
    "gender": "M|F",
    "birth_dt": "1990-01-01",
    "height": 165,
    "weight": 55,
    "allergies": ["땅콩", "해산물"],
    "chron_diseases": ["고혈압", "당뇨"]
  }
  ```
- **사용처**:
  - Step 1: 성별/나이 기반 KDRI 적용
  - Step 3: 성별별 제품 추천 (여성용/남성용)

### 7) 의약품-영양소 상호작용 Knowledge Base
- **위치**: `/lpi_kb.npz` (벡터 임베딩), `/lpi_kb_texts.json` (원문)
- **크기**: 252개 청크, 1536차원 벡터
- **임베딩 모델**: global.cohere.embed-v4:0 (AWS Bedrock)
- **사용처**: Step 1 KB 검색 → 약물 상호작용 정보 프롬프트 주입
- **예시 내용**:
  - 와파린 + 비타민K 상호작용
  - 스타틴 + CoQ10 상호작용
  - 항생제 + 철분/칼슘 흡수 저하
  - 갑상선약 + 칼슘/철분 2시간 간격 필요

### 8) 챗봇 재분석 컨텍스트
- **입력 필드**: `chat_history`, `previous_analysis`, `new_purpose`
- **chat_history**: [{"role": "user"|"assistant", "content": "..."}]
- **previous_analysis**: {"step1": {...}, "step2": {...}, "step3": {...}}
- **new_purpose**: 새로운 섭취 목적
- **사용처**:
  - Step 1: 이전 분석과 달라진 이유 설명
  - 재분석 시 new_purpose 없으면 previous_analysis.summary에서 기존 목적 파악

---

## 6. 핵심 로직 파일 위치

### 메인 서비스 파일

| 파일명 | 경로 | 역할 |
|--------|------|------|
| **analysis_agent.py** | `/app/services/` | 핵심 에이전트 로직 (3단계 파이프라인 조율) |
| **invocations.py** | `/app/api/routes/` | FastAPI 엔드포인트 (POST /invocations) |
| **handler.py** | `/lambdas/action_nutrient_calc/` | Lambda 갭 계산 함수 |
| **kb_retriever.py** | `/app/services/` | 의약품-영양소 KB 검색 |
| **analysis.py** | `/app/schemas/` | Pydantic 데이터 스키마 (요청/응답) |
| **main.py** | `/app/` | FastAPI 앱 초기화 |
| **config.py** | `/app/core/` | 설정 관리 (LLM 제공자, 환경변수) |

### 데이터 파일

| 파일명 | 크기 | 역할 |
|--------|------|------|
| **lpi_kb.npz** | ~920KB | 임베딩 벡터 (252개 청크 × 1536차원) |
| **lpi_kb_texts.json** | ~224KB | KB 청크 원문 + 메타데이터 |

### 구조도

```
/codecaine-python-analysisagent/
├── app/
│   ├── main.py                     # FastAPI 앱 진입점
│   ├── api/
│   │   └── routes/
│   │       └── invocations.py      # POST /invocations 엔드포인트
│   ├── services/
│   │   ├── analysis_agent.py       # [핵심] 3단계 에이전트 로직
│   │   └── kb_retriever.py         # [핵심] KB 검색 로직
│   ├── schemas/
│   │   └── analysis.py             # [핵심] 데이터 스키마
│   ├── core/
│   │   └── config.py               # 설정 관리
│   └── metrics.py                  # Prometheus 메트릭
├── lambdas/
│   └── action_nutrient_calc/
│       └── handler.py              # [핵심] Lambda 갭 계산
├── lpi_kb.npz                      # [데이터] 임베딩 벡터
├── lpi_kb_texts.json               # [데이터] KB 원문
├── README.md                        # 사용 설명서
└── Dockerfile                       # Docker 배포 설정
```

---

## 7. 각 Step별 상세 로직

### Step 1: 필요 영양소 분석

**파일**: `/app/services/analysis_agent.py` (line 173-194)

```python
# 1. KB 검색
kb_context = retrieve_drug_interactions(req.medication_info, [])
if kb_context:
    step1_user_prompt += f"\n\n[의약품-영양소 상호작용 참고 정보]\n{kb_context}"

# 2. 프롬프트 구성
step1_user_prompt = _build_step1_prompt(req)

# 3. LLM 호출
step1_raw = _call_llm(
    system=SYSTEM_PROMPT_STEP1,
    user=step1_user_prompt,
)

# 4. JSON 파싱
step1_data = _parse_json(step1_raw)
required_nutrients = [
    n for n in step1_data.get("required_nutrients", [])
    if n.get("rda_amount") and n.get("unit")
]
summary = step1_data.get("summary", {})
```

**중요 필터링**:
- rda_amount가 없거나 unit이 없는 영양소는 제외
- 약물 상호작용으로 주의할 영양소 → required_nutrients 제외, key_concerns에 명시

---

### Step 2: 영양소 갭 계산

**파일**: `/lambdas/action_nutrient_calc/handler.py` (line 62-117)

```python
# 1. 섭취량 집계
intake_map = build_intake_map(current_supplements)

# 2. 각 required_nutrient별 갭 계산
for req in required_nutrients:
    name_ko = req["name_ko"]
    req_unit = req["unit"]
    req_rda = Decimal(str(req["rda_amount"]))
    
    # 3. 단위 변환
    current_mg = to_mg(
        intake_map.get(name_ko, Decimal("0")),
        req_unit,
        unit_cache,
        name_ko
    )
    rda_mg = to_mg(req_rda, req_unit, unit_cache, name_ko)
    
    # 4. 갭 계산 (mg 단위)
    gap_mg = max(Decimal("0"), rda_mg - current_mg)
    
    # 5. 반올림
    gap_mg = gap_mg.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    
    gaps.append({
        "nutrient_id": req.get("nutrient_id"),
        "name_ko": name_ko,
        "unit": "mg",          # 항상 mg으로 정규화
        "current_amount": str(current_mg.quantize(Decimal("0.0001"))),
        "gap_amount": str(gap_mg),
        "rda_amount": str(rda_mg.quantize(Decimal("0.0001")))
    })
```

**단위 변환 로직**:
```python
def to_mg(amount, unit, unit_cache, nutrient_name_ko=""):
    if not unit or unit in {'mg', 'MG'}:
        return amount
    
    # mcg/µg/μg → mg
    if unit.lower() in ('mcg', 'µg', 'μg'):
        factor = unit_cache.get('mcg', Decimal('0.001'))
        return amount * factor
    
    # IU → mg (영양소 이름 기반)
    if unit == 'IU':
        name_normalized = nutrient_name_ko.replace(" ", "")
        factor = (
            unit_cache.get(nutrient_name_ko) or
            unit_cache.get(name_normalized)
        )
        if factor is None:
            logger.warning(f"factor 없음: {nutrient_name_ko}")
            return amount
        return amount * factor
    
    return amount
```

---

### Step 3: 영양제 추천

**파일**: `/app/services/analysis_agent.py` (line 206-214)

```python
# 1. 프롬프트 구성
step3_prompt = _build_step3_prompt(gaps, req.products or [])

# 2. LLM 호출
step3_raw = _call_llm(
    system=SYSTEM_PROMPT_STEP3,
    user=step3_prompt
)

# 3. JSON 파싱
step3_data = _parse_json(step3_raw)
recommendations = step3_data.get("recommendations", [])
```

**프롬프트 구성**:
```python
def _build_step3_prompt(gaps: list[dict], products: list[dict]) -> str:
    # gap_amount > 0인 갭만 포함
    active_gaps = [g for g in gaps if float(g.get("gap_amount", 0)) > 0]
    
    parts = [
        "아래 영양소 갭을 채울 수 있는 최적의 영양제를 추천하세요.",
        f"\n영양소 갭 목록:\n{json.dumps(active_gaps, ensure_ascii=False, indent=2)}",
    ]
    
    if products:
        parts.append(
            f"\n추천 가능한 영양제 목록:\n{json.dumps(products, ensure_ascii=False, indent=2)}"
        )
    
    return "\n".join(parts)
```

---

## 8. LLM 제공자 전환 (멀티 프로바이더 지원)

**환경변수**: `LLM_PROVIDER` (bedrock / anthropic / openai)

**파일**: `/app/services/analysis_agent.py` (line 161-171, 232-280)

```python
class AnalysisAgent:
    def __init__(self):
        if settings.LLM_PROVIDER == "openai":
            from openai import OpenAI
            self.llm_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        elif settings.LLM_PROVIDER == "anthropic":
            import anthropic
            self.llm_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        else:  # bedrock (기본)
            self.llm_client = boto3.client("bedrock-runtime")

def _call_llm(self, system: str, user: str) -> str:
    if settings.LLM_PROVIDER == "openai":
        return self._call_openai(system, user)
    elif settings.LLM_PROVIDER == "anthropic":
        return self._call_anthropic(system, user)
    return self._call_bedrock(system, user)
```

**각 프로바이더별 호출**:

1. **OpenAI**
   ```python
   response = self.llm_client.chat.completions.create(
       model=settings.OPENAI_MODEL_ID,  # "gpt-4o"
       messages=[...],
       max_tokens=2048,
       response_format={"type": "json_object"}
   )
   ```

2. **Anthropic API**
   ```python
   message = self.llm_client.messages.create(
       model=settings.ANTHROPIC_MODEL_ID,  # "claude-sonnet-4-5"
       max_tokens=2048,
       system=system,
       messages=[{"role": "user", "content": user}]
   )
   ```

3. **AWS Bedrock** (운영)
   ```python
   response = self.llm_client.invoke_model(
       modelId=settings.BEDROCK_MODEL_ID,  # "anthropic.claude-3-5-sonnet-20240620-v1:0"
       body=json.dumps({
           "anthropic_version": "bedrock-2023-05-31",
           "max_tokens": 2048,
           "system": system,
           "messages": [{"role": "user", "content": user}]
       })
   )
   ```

---

## 9. Knowledge Base 검색 상세

**파일**: `/app/services/kb_retriever.py`

### KB 구조
- **lpi_kb.npz**: (252, 1536) float32 정규화된 벡터 행렬
- **lpi_kb_texts.json**: 
  ```json
  {
    "documents": ["청크1", "청크2", ...],
    "metadatas": [{...}, {...}, ...]
  }
  ```

### 검색 과정

```python
def retrieve_drug_interactions(medications: list[dict], required_nutrients: list[str]) -> str:
    # 1. 약물명 추출
    med_names = [m.get("name", "") for m in medications if m.get("name")]
    
    # 2. 약물별 개별 쿼리
    for med in med_names:
        context = retrieve(f"{med} drug nutrient vitamin interaction")
    
    # 3. 통합 쿼리 (약물 + 영양소)
    if med_names and required_nutrients:
        context = retrieve(f"{' '.join(med_names)} {' '.join(required_nutrients)} interaction")
    
    # 4. 결과 합치기
    return "\n\n".join(contexts)

def retrieve(query: str) -> str:
    # 1. 쿼리 임베딩 (Cohere Bedrock)
    query_vec = _embed_query(query)  # (1536,) 정규화 벡터
    
    # 2. cosine similarity 계산
    similarities = vectors @ query_vec  # (252,) 유사도 점수
    top_indices = np.argsort(similarities)[::-1][:KB_TOP_K]  # Top 3
    
    # 3. 상위 K개 청크 추출
    docs = [texts["documents"][i] for i in top_indices]
    
    # 4. 프롬프트 주입용 텍스트 반환
    return "\n\n".join(docs)
```

**설정**:
- **KB_TOP_K**: 3 (기본값)
- **Embedding 모델**: global.cohere.embed-v4:0
- **유사도 계산**: cosine similarity (정규화된 벡터 × dot product)
- **다국어 지원**: Cohere embed-v4의 크로스링귀얼 특성으로 한국어 쿼리도 영어 KB 검색 가능

---

## 10. 메트릭 수집

**파일**: `/app/metrics.py`

**수집 메트릭**:
```python
# 1. 에이전트 호출
agent_invocation_counter      # 호출 횟수 (성공/실패)
agent_latency_histogram       # 응답 시간

# 2. 도구 실행
tool_execution_counter        # 각 도구별 실행 횟수 (KB, LLM, Lambda)
tool_duration_histogram       # 각 도구별 실행 시간

# 3. LLM 토큰
agent_token_input_counter     # 입력 토큰 (LLM별)
agent_token_output_counter    # 출력 토큰 (LLM별)

# 4. KB 검색
kb_context_counter            # KB hit/miss
kb_chunks_retrieved_counter   # 검색된 청크 수
```

---

## 11. 에러 핸들링 및 검증

### JSON 파싱 실패 처리
```python
@staticmethod
def _parse_json(text: str) -> dict:
    # 1. "```json ... ```" 블록 제거
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:].strip()
    
    # 2. 앞뒤 공백 제거 (JSON 블록만 추출)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    
    # 3. 파싱 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 실패: {e}\n원문: {text[:300]}")
        raise ValueError(f"LLM 응답 파싱 실패: {e}") from e
```

### Step 1 필터링
```python
required_nutrients = [
    n for n in step1_data.get("required_nutrients", [])
    if n.get("rda_amount") is not None and n.get("unit") is not None
]
```
- rda_amount나 unit이 없는 영양소 자동 제외
- 약물 상호작용 주의 사항은 key_concerns에만 포함

---

## 12. 운영 고려사항

### 배포 환경
- **컨테이너**: Dockerfile 제공 (FastAPI 앱)
- **LLM**: AWS Bedrock AgentCore Runtime (본운영)
- **Lambda**: action-nutrient-calc (갭 계산)
- **모니터링**: Prometheus 메트릭 (Cloudwatch 연동 가능)

### 성능
- **Step 1**: LLM 호출 (2-3초)
- **Step 2**: Lambda 호출 (0.5-1초)
- **Step 3**: LLM 호출 (1-2초)
- **KB 검색**: Cohere 임베딩 + 벡터 연산 (1-2초)
- **전체**: 약 5-10초/요청

### 데이터 보안
- CODEF 건강검진 데이터 암호화 전송
- 사용자 식별: Cognito ID 기반
- 의약품 정보 민감도 높음 → 로깅 최소화

---

## 13. 주의사항 및 제약사항

### 재분석 (Chat History 지원)
- `previous_analysis` 필드로 이전 분석 결과 제공 시 비교 분석 가능
- `new_purpose` 필드로 새로운 섭취 목적 지정 가능
- `chat_history` 필드 형식은 아직 미확정 (추후 챗봇 서비스 구현 후 확정 예정)

### 제품 추천의 한계
- **제공된 제품 목록에서만 추천** (임의로 만들어낼 수 없음)
- App이 DB에서 products + product_nutrients 조회 후 전달해야 함
- products 필드가 없으면 Step 3 추천 건너뜀

### 단위 변환의 한계
- unit_cache에 없는 영양소의 IU 변환 불가 → 경고 로그 출력 후 amount 그대로 사용
- 지원하지 않는 단위 → 경고 로그 출력 후 amount 그대로 사용

---

## 14. 확장 가능성

### 새로운 LLM 제공자 추가
```python
# config.py에 설정 추가
NEW_LLM_API_KEY: str | None = None
NEW_LLM_MODEL_ID: str = "model-id"

# analysis_agent.py에 메서드 추가
def _call_new_llm(self, system: str, user: str) -> str:
    # 구현...

# _call_llm()에 분기 추가
elif settings.LLM_PROVIDER == "new_llm":
    return self._call_new_llm(system, user)
```

### KB 업데이트
- 새로운 의약품-영양소 상호작용 정보 추가 시
- lpi_kb.npz, lpi_kb_texts.json 재생성
- 컨테이너 이미지 재빌드 후 배포

### Step 간 프롬프트 수정
- SYSTEM_PROMPT_STEP1, SYSTEM_PROMPT_STEP3 상수 수정
- 코드 재배포 필요 (환경변수로 관리하면 배포 회피 가능)

---

## 요약

| 항목 | 내용 |
|------|------|
| **입력** | cognito_id, 건강검진 데이터(CODEF), 의약품 정보, 현재 영양제, 단위 환산표, 제품 목록, 사용자 프로필, (재분석 시) 이전 분석/대화 내역 |
| **출력** | step1 (필요 영양소 + 평가/조언), step2 (영양소 갭), step3 (영양제 추천) |
| **구조** | 3단계 LLM+Lambda 파이프라인 (KB 검색 + LLM 분석 + Lambda 계산 + LLM 추천) |
| **데이터** | 혈액검사 수치, 약물 정보, 현재 영양제, 의약품-영양소 KB (252개 벡터), 영양제 제품 정보 |
| **핵심 파일** | analysis_agent.py, handler.py, kb_retriever.py, analysis.py, config.py |
| **LLM 제공자** | AWS Bedrock (운영) / Anthropic API / OpenAI API (테스트) |
| **성능** | 약 5-10초/요청 |
| **메트릭** | 호출 횟수, 응답 시간, 도구별 실행 시간, 토큰 사용량, KB 검색 결과 |

