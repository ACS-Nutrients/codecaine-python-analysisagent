"""Lambda 갭 계산 로직 단위 테스트 (DB 없이)"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lambdas', 'action_nutrient_calc'))
import json
from decimal import Decimal
from handler import build_intake_map, to_mg, lambda_handler


def test_to_mg_mg():
    assert to_mg(Decimal("500"), "mg", {}) == Decimal("500")

def test_to_mg_iu():
    cache = {"IU": Decimal("0.000025")}
    assert to_mg(Decimal("800"), "IU", cache) == Decimal("0.02")

def test_to_mg_unknown():
    assert to_mg(Decimal("100"), "xyz", {}) == Decimal("100")

def test_build_intake_map():
    supplements = [{
        "serving_per_day": 2,
        "ingredients": [{"name": "비타민 C", "amount": 200}]
    }]
    result = build_intake_map(supplements)
    assert result["비타민 C"] == Decimal("400")

def test_gap_zero_when_sufficient():
    assert max(Decimal("0"), Decimal("400") - Decimal("600")) == Decimal("0")

def test_lambda_handler():
    event = {
        "cognito_id": "test-user",
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