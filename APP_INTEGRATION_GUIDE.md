# Analysis Agent 통합 가이드

App에서 AgentCore Runtime으로 배포된 Analysis Agent를 호출하고
결과를 RDS에 저장하기 위해 수정해야 할 내용입니다.

---

## 전체 흐름

```
App
  │
  ├─► 1. DB에서 current_supplements, unit_cache 조회
  │
  ├─► 2. invoke_agent_runtime() 호출 (payload에 위 데이터 포함)
  │         └─► AgentCore Runtime (컨테이너)
  │               ├─ Step 1: Bedrock LLM → required_nutrients
  │               ├─ Step 2: Lambda     → gaps 계산
  │               └─ Step 3: Bedrock LLM → recommendations
  │               └─► 최종 JSON 반환 { step1, step2, step3 }
  │
  └─► 3. 응답 파싱 후 RDS에 저장
        ├─ step1 → analysis_result 테이블
        ├─ step2 → nutrient_gap 테이블
        └─ step3 → recommendations 테이블
```

---

## 1. 환경변수 추가

App의 `.env` 또는 ECS Task Definition에 아래 값 추가:

```env
AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-northeast-2:YOUR_ACCOUNT:runtime/analysis-agent-XXXXXX
AWS_REGION=ap-northeast-2
```

---

## 2. IAM 권한 추가

App이 실행되는 ECS Task Role에 아래 권한 추가:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:InvokeAgentRuntime"
  ],
  "Resource": "arn:aws:bedrock-agentcore:ap-northeast-2:*:runtime/*"
}
```

---

## 3. DB 마이그레이션

Analysis Agent 배포 전에 RDS에 아래 마이그레이션 실행:

```bash
psql $DATABASE_URL -f migrations/fix_schema.sql
```

변경 내용:
- `anaysis_current_ingredients` → `analysis_current_ingredients` (오타 수정)
- `nutrient_gap.current_amount`, `gap_amount` → `NUMERIC(10,4)` (소수점 보존)
- `product_nutrients.amount_per_serving`, `amount_per_day` → `NUMERIC(10,4)`
- `recommendations` 테이블에 FK 추가

---

## 4. App 코드 수정

### 4-1. AgentCore 호출 서비스 추가

분석 서비스 내에 아래 파일을 추가하세요.

```python
# services/agentcore_client.py

import json
import boto3
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# App의 기존 ORM 모델 import (경로는 프로젝트에 맞게 수정)
from models import (
    AnalysisSupplements,
    AnalysisCurrentIngredients,
    UnitConvertor,
    AnalysisResult,
    NutrientGap,
    Recommendation,
)
from datetime import datetime, timezone


class AgentCoreClient:
    def __init__(self, db: AsyncSession, runtime_arn: str, region: str = "ap-northeast-2"):
        self.db = db
        self.runtime_arn = runtime_arn
        self.client = boto3.client("bedrock-agentcore", region_name=region)

    async def run_analysis(
        self,
        cognito_id: str,
        intake_purpose: str,
        codef_health_data: dict | None = None,
        medication_info: list[dict] | None = None,
    ) -> dict:
        # 1. DB에서 필요한 데이터 조회
        # DB 연결 정보가 바뀌어도 App의 .env만 변경하면 됨 (코드 수정 불필요)
        current_supplements = await self._get_supplements(cognito_id)
        unit_cache          = await self._get_unit_cache()
        products            = await self._get_products()

        # 2. AgentCore Runtime 호출
        payload = {
            "cognito_id":          cognito_id,
            "intake_purpose":      intake_purpose,
            "codef_health_data":   codef_health_data,
            "medication_info":     medication_info,
            "current_supplements": current_supplements,
            "unit_cache":          unit_cache,
            "products":            products,   # Step 3 추천에 사용
        }

        response = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            payload=json.dumps(payload, ensure_ascii=False),
        )

        # 3. 응답 파싱
        result = json.loads(response["response"])

        # 4. RDS에 저장
        result_id = await self._save_analysis_result(
            cognito_id=cognito_id,
            required_nutrients=result["step1"]["required_nutrients"],
            summary=result["step1"]["summary"],
        )
        await self._save_nutrient_gaps(
            cognito_id=cognito_id,
            result_id=result_id,
            gaps=result["step2"]["gaps"],
        )
        await self._save_recommendations(
            cognito_id=cognito_id,
            result_id=result_id,
            recommendations=result["step3"]["recommendations"],
        )
        await self.db.commit()

        return {"result_id": result_id, **result}

    # ── DB 조회 ─────────────────────────────────────────────────

    async def _get_supplements(self, cognito_id: str) -> list[dict]:
        """
        복제 DB에서 현재 활성 영양제 조회.
        analysis_supplements + analysis_current_ingredients JOIN.
        """
        result = await self.db.execute(
            select(AnalysisSupplements)
            .where(
                AnalysisSupplements.cognito_id == cognito_id,
                AnalysisSupplements.ans_is_active == True,
            )
            .options(selectinload(AnalysisSupplements.ingredients))
        )
        supplements = result.scalars().all()
        return [
            {
                "product_name":    s.ans_product_name,
                "serving_per_day": s.ans_serving_per_day,
                "ingredients": [
                    {
                        "name":   i.ans_ingredient_name,
                        "amount": float(i.ans_nutrient_amount or 0),
                    }
                    for i in s.ingredients
                ],
            }
            for s in supplements
        ]

    async def _get_unit_cache(self) -> dict:
        """unit_convertor 테이블 전체 조회 → { "비타민D": "0.000025", "mcg": "0.001", ... }"""
        result = await self.db.execute(select(UnitConvertor))
        return {
            row.vitamin_name: str(row.convert_unit)
            for row in result.scalars().all()
        }

    async def _get_products(self) -> list[dict]:
        """
        products + product_nutrients 조회 → Step 3 추천 LLM에 전달.
        DB가 바뀌어도 App의 .env만 변경하면 되고 이 코드는 수정 불필요.
        """
        from models import Product, ProductNutrient, Nutrient
        result = await self.db.execute(
            select(Product)
            .options(
                selectinload(Product.nutrients).selectinload(ProductNutrient.nutrient)
            )
        )
        products = result.scalars().all()
        return [
            {
                "product_id":    p.product_id,
                "product_name":  p.product_name,
                "product_brand": p.product_brand,
                "serving_per_day": p.serving_per_day,
                "nutrients": [
                    {
                        "name_ko":         pn.nutrient.name_ko if pn.nutrient else None,
                        "name_en":         pn.nutrient.name_en if pn.nutrient else None,
                        "unit":            pn.unit,
                        "amount_per_day":  pn.amount_per_day,
                    }
                    for pn in p.nutrients
                    if pn.amount_per_day and pn.amount_per_day > 0
                ],
            }
            for p in products
        ]

    # ── DB 저장 ─────────────────────────────────────────────────

    async def _save_analysis_result(
        self, cognito_id: str, required_nutrients: list, summary: dict
    ) -> int:
        record = AnalysisResult(
            cognito_id=cognito_id,
            summary_jsonb={
                "summary":            summary,
                "required_nutrients": required_nutrients,
            },
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(record)
        await self.db.flush()
        return record.result_id

    async def _save_nutrient_gaps(
        self, cognito_id: str, result_id: int, gaps: list[dict]
    ) -> None:
        now = datetime.now(timezone.utc)
        for gap in gaps:
            if not gap.get("nutrient_id"):
                # nutrient_id가 없으면 nutrients 테이블에서 name_ko로 조회
                from models import Nutrient
                res = await self.db.execute(
                    select(Nutrient).where(Nutrient.name_ko == gap["name_ko"])
                )
                nutrient = res.scalar_one_or_none()
                if not nutrient:
                    continue
                gap["nutrient_id"] = nutrient.nutrient_id

            self.db.add(NutrientGap(
                result_id=result_id,
                cognito_id=cognito_id,
                nutrient_id=gap["nutrient_id"],
                current_amount=Decimal(str(gap["current_amount"])),
                gap_amount=Decimal(str(gap["gap_amount"])),
                created_at=now,
            ))
        await self.db.flush()

    async def _save_recommendations(
        self, cognito_id: str, result_id: int, recommendations: list[dict]
    ) -> None:
        now = datetime.now(timezone.utc)
        for rec in recommendations:
            self.db.add(Recommendation(
                product_id=rec["product_id"],
                result_id=result_id,
                cognito_id=cognito_id,
                recommend_serving=rec.get("recommend_serving", 1),
                rank=rec.get("rank"),
                created_at=now,
            ))
        await self.db.flush()
```

### 4-2. 기존 분석 API 엔드포인트 수정

기존에 분석을 처리하던 엔드포인트를 아래와 같이 수정하세요.

```python
# 기존 코드 (수정 전)
@router.post("/analysis/run")
async def run_analysis(req: AnalysisRunRequest, db: AsyncSession = Depends(get_db)):
    # 기존 분석 로직...
    pass

# 수정 후
import os
from services.agentcore_client import AgentCoreClient

AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]

@router.post("/analysis/run")
async def run_analysis(req: AnalysisRunRequest, db: AsyncSession = Depends(get_db)):
    client = AgentCoreClient(
        db=db,
        runtime_arn=AGENTCORE_RUNTIME_ARN,
        region=os.environ.get("AWS_REGION", "ap-northeast-2"),
    )
    return await client.run_analysis(
        cognito_id=req.cognito_id,
        intake_purpose=req.intake_purpose,
        codef_health_data=req.codef_health_data,
        medication_info=req.medication_info,
    )
```

---

## 5. AgentCore Runtime 응답 형식

App이 받는 최종 JSON 구조:

```json
{
  "cognito_id": "user-cognito-id",
  "step1": {
    "required_nutrients": [
      {
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "rda_amount": 800,
        "unit": "IU",
        "reason": "검진 결과 결핍 위험"
      }
    ],
    "summary": {
      "overall_assessment": "전반적인 영양 상태 평가",
      "key_concerns": ["우려사항1"],
      "lifestyle_notes": "생활습관 메모"
    }
  },
  "step2": {
    "gaps": [
      {
        "nutrient_id": null,
        "name_ko": "비타민 D",
        "name_en": "Vitamin D",
        "unit": "mg",
        "current_amount": "0.0100",
        "gap_amount": "0.0100",
        "rda_amount": "0.0200"
      }
    ]
  },
  "step3": {
    "recommendations": [
      {
        "rank": 1,
        "product_id": 12,
        "product_name": "제품명",
        "product_brand": "브랜드",
        "recommend_serving": 2,
        "serving_per_day": 2,
        "covered_nutrients": ["비타민 D"]
      }
    ]
  }
}
```

> `step2.gaps[].nutrient_id`는 Lambda가 DB 접근 없이 계산하므로 `null`일 수 있음.
> App의 `_save_nutrient_gaps()`에서 `name_ko`로 `nutrients` 테이블 조회하여 매핑.

---

## 6. 다른 서비스에서 사용하는 경우

Analysis Agent는 다른 MSA 서비스에서도 동일하게 사용 가능합니다.
각 서비스에서 `AgentCoreClient`를 동일하게 구현하여 호출하면 됩니다.

```python
# 다른 서비스에서 사용 예시
client = AgentCoreClient(
    db=db,
    runtime_arn=os.environ["AGENTCORE_RUNTIME_ARN"],
)
result = await client.run_analysis(
    cognito_id="user-123",
    intake_purpose="피로 회복",
    codef_health_data=health_data,
)
```

---

## 7. AgentCore Runtime 배포 순서

```bash
# 1. Lambda 배포 (VPC 설정 없음 — DB 접근 없으므로)
cd lambdas/action_nutrient_calc
zip function.zip handler.py
aws lambda create-function \
  --function-name action-nutrient-calc \
  --runtime python3.12 \
  --handler handler.lambda_handler \
  --zip-file fileb://function.zip \
  --role arn:aws:iam::ACCOUNT:role/lambda-basic-role

# 2. AgentCore Runtime 배포 (ECR 이미지)
agentcore configure --entrypoint app/main.py
agentcore deploy --image-tag v1.0.0

# 3. 배포 완료 후 Runtime ARN 확인
agentcore status
# → agentRuntimeArn 값을 App 환경변수 AGENTCORE_RUNTIME_ARN에 설정
```
