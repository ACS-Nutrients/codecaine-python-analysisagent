"""Lambda 갭 계산 로직 확장 단위 테스트"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lambdas', 'action_nutrient_calc'))

from decimal import Decimal
from handler import build_intake_map, to_mg, lambda_handler


# ── to_mg 단위 변환 ──────────────────────────────────────────────

class TestToMg:

    def test_mg_passthrough(self):
        """mg 단위는 변환 없이 그대로"""
        assert to_mg(Decimal("500"), "mg", {}) == Decimal("500")

    def test_mg_uppercase(self):
        """MG 대문자도 통과"""
        assert to_mg(Decimal("300"), "MG", {}) == Decimal("300")

    def test_mg_no_unit(self):
        """unit 없으면 그대로"""
        assert to_mg(Decimal("100"), "", {}) == Decimal("100")
        assert to_mg(Decimal("100"), None, {}) == Decimal("100")

    def test_mcg_with_cache(self):
        """mcg → mg: cache의 factor 사용"""
        cache = {"mcg": Decimal("0.001")}
        result = to_mg(Decimal("500"), "mcg", cache)
        assert result == Decimal("0.5")

    def test_mcg_without_cache_uses_default(self):
        """mcg cache 없으면 기본값 0.001 사용"""
        result = to_mg(Decimal("1000"), "mcg", {})
        assert result == Decimal("1.0")

    def test_mcg_unicode_variants(self):
        """µg, μg 모두 mcg로 처리"""
        cache = {"mcg": Decimal("0.001")}
        assert to_mg(Decimal("500"), "µg", cache) == Decimal("0.5")
        assert to_mg(Decimal("500"), "μg", cache) == Decimal("0.5")

    def test_iu_vitamin_d(self):
        """IU → mg: 비타민D factor"""
        cache = {"비타민D": Decimal("0.000025")}
        result = to_mg(Decimal("800"), "IU", cache, "비타민D")
        assert result == Decimal("0.02")

    def test_iu_vitamin_d_with_space(self):
        """'비타민 D' (공백 있음)도 '비타민D'로 정규화해서 조회"""
        cache = {"비타민D": Decimal("0.000025")}
        result = to_mg(Decimal("800"), "IU", cache, "비타민 D")
        assert result == Decimal("0.02")

    def test_iu_vitamin_a(self):
        """IU → mg: 비타민A factor"""
        cache = {"비타민A": Decimal("0.000030")}
        result = to_mg(Decimal("1000"), "IU", cache, "비타민A")
        assert result == Decimal("0.03")

    def test_iu_vitamin_e(self):
        """IU → mg: 비타민E factor"""
        cache = {"비타민E": Decimal("0.00067")}
        result = to_mg(Decimal("400"), "IU", cache, "비타민E")
        assert result == Decimal("0.268")

    def test_iu_missing_factor_returns_raw(self):
        """IU factor 없으면 변환 없이 원값 반환"""
        result = to_mg(Decimal("800"), "IU", {}, "알수없는영양소")
        assert result == Decimal("800")

    def test_unknown_unit_returns_raw(self):
        """알 수 없는 단위는 그대로"""
        result = to_mg(Decimal("100"), "xyz", {})
        assert result == Decimal("100")


# ── build_intake_map ─────────────────────────────────────────────

class TestBuildIntakeMap:

    def test_single_supplement(self):
        """단일 영양제 섭취량 집계"""
        supplements = [{
            "serving_per_day": 1,
            "ingredients": [{"name": "비타민 C", "amount": 500}]
        }]
        result = build_intake_map(supplements)
        assert result["비타민 C"] == Decimal("500")

    def test_serving_multiplier(self):
        """serving_per_day 배수 적용"""
        supplements = [{
            "serving_per_day": 2,
            "ingredients": [{"name": "비타민 C", "amount": 200}]
        }]
        result = build_intake_map(supplements)
        assert result["비타민 C"] == Decimal("400")

    def test_multiple_supplements_same_nutrient(self):
        """여러 영양제에서 같은 영양소 누적 합산"""
        supplements = [
            {"serving_per_day": 1, "ingredients": [{"name": "비타민 D", "amount": 400}]},
            {"serving_per_day": 1, "ingredients": [{"name": "비타민 D", "amount": 600}]},
        ]
        result = build_intake_map(supplements)
        assert result["비타민 D"] == Decimal("1000")

    def test_multiple_nutrients_in_one_supplement(self):
        """한 영양제에 여러 영양소"""
        supplements = [{
            "serving_per_day": 1,
            "ingredients": [
                {"name": "비타민 C", "amount": 500},
                {"name": "아연", "amount": 10},
            ]
        }]
        result = build_intake_map(supplements)
        assert result["비타민 C"] == Decimal("500")
        assert result["아연"] == Decimal("10")

    def test_empty_supplements(self):
        """영양제 없으면 빈 dict"""
        assert build_intake_map([]) == {}

    def test_missing_amount_skipped(self):
        """amount 없는 항목은 무시"""
        supplements = [{
            "serving_per_day": 1,
            "ingredients": [{"name": "비타민 C", "amount": None}]
        }]
        result = build_intake_map(supplements)
        assert "비타민 C" not in result

    def test_missing_name_skipped(self):
        """name 없는 항목은 무시"""
        supplements = [{
            "serving_per_day": 1,
            "ingredients": [{"name": "", "amount": 100}]
        }]
        result = build_intake_map(supplements)
        assert result == {}

    def test_default_serving_per_day(self):
        """serving_per_day 없으면 기본값 1 적용"""
        supplements = [{
            "ingredients": [{"name": "마그네슘", "amount": 300}]
        }]
        result = build_intake_map(supplements)
        assert result["마그네슘"] == Decimal("300")


# ── lambda_handler 통합 ──────────────────────────────────────────

class TestLambdaHandler:

    def test_basic_gap(self):
        """기본 갭 계산 — 현재 섭취 없음"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 C", "name_en": "Vitamin C", "rda_amount": 1000, "unit": "mg"}
            ],
            "current_supplements": [],
            "unit_cache": {}
        }
        result = lambda_handler(event, None)
        gap = result["gaps"][0]
        assert gap["gap_amount"] == "1000.0000"
        assert gap["current_amount"] == "0.0000"

    def test_gap_reduced_by_current_intake(self):
        """현재 섭취량만큼 갭 차감"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 C", "name_en": "Vitamin C", "rda_amount": 1000, "unit": "mg"}
            ],
            "current_supplements": [{
                "serving_per_day": 1,
                "ingredients": [{"name": "비타민 C", "amount": 500}]
            }],
            "unit_cache": {}
        }
        result = lambda_handler(event, None)
        assert result["gaps"][0]["gap_amount"] == "500.0000"

    def test_gap_zero_when_sufficient(self):
        """현재 섭취가 RDA 이상이면 갭은 0"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 C", "name_en": "Vitamin C", "rda_amount": 500, "unit": "mg"}
            ],
            "current_supplements": [{
                "serving_per_day": 1,
                "ingredients": [{"name": "비타민 C", "amount": 600}]
            }],
            "unit_cache": {}
        }
        result = lambda_handler(event, None)
        assert result["gaps"][0]["gap_amount"] == "0.0000"

    def test_iu_unit_conversion_in_gap(self):
        """IU 단위 영양소 갭 계산 — mg로 정규화"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 D", "name_en": "Vitamin D", "rda_amount": 800, "unit": "IU"}
            ],
            "current_supplements": [{
                "serving_per_day": 1,
                "ingredients": [{"name": "비타민 D", "amount": 400, "unit": "IU"}]
            }],
            "unit_cache": {"비타민D": "0.000025", "비타민 D": "0.000025"}
        }
        result = lambda_handler(event, None)
        gap = result["gaps"][0]
        # RDA 800 IU = 0.02mg, 현재 400 IU = 0.01mg, 갭 = 0.01mg
        assert gap["rda_amount"] == "0.0200"
        assert gap["current_amount"] == "0.0100"
        assert gap["gap_amount"] == "0.0100"

    def test_mcg_unit_conversion_in_gap(self):
        """mcg 단위 영양소 갭 계산"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 B12", "rda_amount": 2.4, "unit": "mcg"}
            ],
            "current_supplements": [],
            "unit_cache": {"mcg": "0.001"}
        }
        result = lambda_handler(event, None)
        gap = result["gaps"][0]
        # 2.4mcg = 0.0024mg
        assert gap["rda_amount"] == "0.0024"
        assert gap["gap_amount"] == "0.0024"

    def test_multiple_nutrients(self):
        """여러 영양소 동시 처리"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 C", "rda_amount": 1000, "unit": "mg"},
                {"name_ko": "아연", "rda_amount": 8, "unit": "mg"},
            ],
            "current_supplements": [],
            "unit_cache": {}
        }
        result = lambda_handler(event, None)
        assert len(result["gaps"]) == 2
        names = [g["name_ko"] for g in result["gaps"]]
        assert "비타민 C" in names
        assert "아연" in names

    def test_output_unit_always_mg(self):
        """출력 단위는 항상 mg"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [
                {"name_ko": "비타민 D", "rda_amount": 800, "unit": "IU"}
            ],
            "current_supplements": [],
            "unit_cache": {"비타민D": "0.000025"}
        }
        result = lambda_handler(event, None)
        assert result["gaps"][0]["unit"] == "mg"

    def test_empty_required_nutrients(self):
        """required_nutrients 비어있으면 gaps도 빈 배열"""
        event = {
            "cognito_id": "test",
            "required_nutrients": [],
            "current_supplements": [],
            "unit_cache": {}
        }
        result = lambda_handler(event, None)
        assert result["gaps"] == []
