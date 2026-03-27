# calculate 리팩토링 명세

> 기존 `/calculate` 엔드포인트 수정 사항 정리

---

## 변경 배경

- CODEF 정보를 user 서비스에서 조회하던 방식 → 프론트에서 직접 받는 방식으로 변경
- 프론트 건강정보 입력 폼에서 수집하는 데이터 전체를 백엔드로 전달
  - 주요 검사 항목 (`exam_items`) — 기존에 누락
  - 처방 약물 정보 (`prescription_data`) — 기존에 누락
- `start_analysis()` 내 `hd` 변수 미정의 버그 수정

---

## 1. `app/schemas/analysis.py` - AnalysisCalculateRequest 수정

```python
class ExamItem(BaseModel):
    name: str             # 항목명 (예: 공복혈당, 혈압)
    value: str            # 수치 (예: 103, 110/66)
    unit: str             # 단위 (예: mg/dL, mmHg)


class PrescriptionItem(BaseModel):
    name: str             # 약품명 (예: 펠로엔정+)
    dose: str             # 용량 (예: 4)
    usage: str            # 용도/카테고리 (예: 해열,+진통,+소염제)


class HealthCheckData(BaseModel):
    exam_date: str
    gender: int           # 1: 남성, 2: 여성
    age: int
    height: float
    weight: float
    exam_items: List[ExamItem] = []   # 주요 검사 항목 (추가)


class AnalysisCalculateRequest(BaseModel):
    health_check_data: Optional[HealthCheckData] = None
    prescription_data: Optional[List[PrescriptionItem]] = None
    purposes: Optional[List[str]] = None
```

---

## 2. `app/services/analysis_service.py` - start_analysis() 수정

### 2-1. user 서비스 CODEF 조회 제거 + hd 버그 수정

```python
# 제거
codef = get_codef_data(cognito_id, token)
hd = codef.get("codef_health_data") or health_check_data or {}
medication_info = codef.get("medication_info") or []

# 변경 후
hd = health_check_data or {}
medication_info = prescription_data or []
```

### 2-2. 함수 시그니처 변경

```python
def start_analysis(
    db: Session,
    cognito_id: str,
    purpose: str,
    health_check_data: Dict = None,
    prescription_data: List[Dict] = None,
) -> int:
```

> `token` 파라미터 제거

---

## 3. `app/api/endpoints/analysis.py` - 엔드포인트 수정

```python
@router.post("/calculate", response_model=dict)
def calculate_analysis(
    request: AnalysisCalculateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    cognito_id: str = Depends(get_current_user),
):
    purposes = request.purposes or []
    purpose_str = ", ".join(purposes) if purposes else "건강 유지"

    result_id = analysis_service.start_analysis(
        db=db,
        cognito_id=cognito_id,
        purpose=purpose_str,
        health_check_data=request.health_check_data.model_dump() if request.health_check_data else {},
        prescription_data=[item.model_dump() for item in request.prescription_data] if request.prescription_data else [],
    )
    return {"result_id": result_id, "message": "분석이 완료되었습니다."}
```

> `token=credentials.credentials` 제거

---

## 4. 변경 전후 비교

| | 변경 전 | 변경 후 |
|---|---|---|
| CODEF 소스 | user 서비스 API 호출 | 프론트 request body |
| 건강검진 기본 정보 | `{ exam_date, gender, age, height, weight }` | 동일 + `exam_items` 추가 |
| 주요 검사 항목 | 미전달 | `exam_items` 리스트로 전달 |
| 약물 정보 | user 서비스에서 조회 | 프론트 `prescription_data` 필드 |
| JWT | user 서비스 호출용 전달 | 불필요 (인증만 사용) |
