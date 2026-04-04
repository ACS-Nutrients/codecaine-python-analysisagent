"""
KB 메타데이터 한국어 번역 스크립트
- base_nutrient, interaction_type: 하드코딩 매핑
- extracted_tags: Bedrock API 배치 번역
- 결과: lpi_kb_texts_ko.json (documents + metadatas + ids 완전체)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3

REGION = "ap-northeast-2"
MODEL_ID = "global.anthropic.claude-sonnet-4-6"
BATCH_SIZE = 20
CHECKPOINT_PATH = Path("scripts/translate_metadata_checkpoint.json")
INPUT_EN_PATH = Path("lpi_kb_texts.json")
INPUT_KO_PATH = Path("lpi_kb_texts_ko.json")
OUTPUT_PATH = Path("lpi_kb_texts_ko.json")

# ── base_nutrient 번역 매핑 ──────────────────────────────────────────────────
BASE_NUTRIENT_MAP = {
    "Alcoholic Beverages": "알코올 음료 (Alcoholic Beverages)",
    "Biotin": "비오틴 (Biotin)",
    "Calcium": "칼슘 (Calcium)",
    "Carotenoids": "카로티노이드 (Carotenoids)",
    "Choline": "콜린 (Choline)",
    "Chromium": "크로뮴 (Chromium)",
    "Coenzyme Q10": "코엔자임 Q10 (Coenzyme Q10)",
    "Coffee": "커피 (Coffee)",
    "Copper": "구리 (Copper)",
    "Cruciferous Vegetables": "십자화과 채소 (Cruciferous Vegetables)",
    "Curcumin": "커큐민 (Curcumin)",
    "Essential Fatty Acids": "필수지방산 (Essential Fatty Acids)",
    "Fiber": "식이섬유 (Fiber)",
    "Flavonoids": "플라보노이드 (Flavonoids)",
    "Fluoride": "불소 (Fluoride)",
    "Folate": "엽산 (Folate)",
    "Garlic (Organosulfur Compounds)": "마늘/유기황화합물 (Garlic/Organosulfur Compounds)",
    "Indole-3-Carbinol": "인돌-3-카비놀 (Indole-3-Carbinol)",
    "Iodine": "요오드 (Iodine)",
    "Iron": "철분 (Iron)",
    "Isothiocyanates": "이소티오시아네이트 (Isothiocyanates)",
    "L-Carnitine": "L-카르니틴 (L-Carnitine)",
    "Lipoic Acid": "리포산 (Lipoic Acid)",
    "Magnesium": "마그네슘 (Magnesium)",
    "Manganese": "망간 (Manganese)",
    "Molybdenum": "몰리브덴 (Molybdenum)",
    "Niacin": "나이아신 (Niacin)",
    "Pantothenic Acid": "판토텐산 (Pantothenic Acid)",
    "Phosphorus": "인 (Phosphorus)",
    "Potassium": "칼륨 (Potassium)",
    "Resveratrol": "레스베라트롤 (Resveratrol)",
    "Riboflavin": "리보플래빈 (Riboflavin)",
    "Selenium": "셀레늄 (Selenium)",
    "Sodium Chloride (Salt)": "염화나트륨/소금 (Sodium Chloride/Salt)",
    "Soy Isoflavones": "대두 이소플라본 (Soy Isoflavones)",
    "Tea": "차 (Tea)",
    "Thiamin": "티아민 (Thiamin)",
    "Vitamin A": "비타민 A (Vitamin A)",
    "Vitamin B12": "비타민 B12 (Vitamin B12)",
    "Vitamin B6": "비타민 B6 (Vitamin B6)",
    "Vitamin C": "비타민 C (Vitamin C)",
    "Vitamin D": "비타민 D (Vitamin D)",
    "Vitamin E": "비타민 E (Vitamin E)",
    "Vitamin K": "비타민 K (Vitamin K)",
    "Zinc": "아연 (Zinc)",
}

# ── interaction_type 번역 매핑 ────────────────────────────────────────────────
INTERACTION_TYPE_MAP = {
    "Drug interactions": "약물 상호작용",
    "Medication interactions": "의약품 상호작용",
    "Nutrient interactions": "영양소 상호작용",
    "Nutrient Interactions": "영양소 상호작용",
    "Calcium-nutrient interactions": "칼슘-영양소 상호작용",
    "Conditions that increase the risk of hypokalemia (see alsoDrug interactions;1):": "저칼륨혈증 위험 증가 조건",
}

TRANSLATE_TAGS_PROMPT = """아래는 영양제/약물 상호작용 문서에서 추출된 태그 목록입니다.
각 태그를 한국어로 번역하되, 의학/영양학 전문 용어는 한국어 번역 + 괄호 안에 영문 원어를 표기하세요.
이미 한국어로 된 부분(예: '약물명:', '영양소명:', '질환명:' 등 레이블)은 그대로 유지하고, 영어 용어만 번역하세요.

입력 형식: JSON 배열 (각 원소가 태그 문자열 1개)
출력 형식: 반드시 JSON 배열만 출력, 다른 텍스트 없이

입력:
{texts}

출력 (JSON 배열만):"""


def call_bedrock(prompt: str, client) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()


def load_checkpoint() -> dict[int, str]:
    if CHECKPOINT_PATH.exists():
        return {int(k): v for k, v in json.loads(CHECKPOINT_PATH.read_text()).items()}
    return {}


def save_checkpoint(translated: dict[int, str]):
    CHECKPOINT_PATH.write_text(json.dumps(translated, ensure_ascii=False, indent=2))


def translate_tags_batch(tags: list[str], client) -> list[str]:
    texts_json = json.dumps(tags, ensure_ascii=False, indent=2)
    prompt = TRANSLATE_TAGS_PROMPT.format(texts=texts_json)
    raw = call_bedrock(prompt, client)

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```")[0]

    result = json.loads(raw.strip())
    if isinstance(result, list) and len(result) == len(tags):
        return result
    raise ValueError(f"길이 불일치: 입력 {len(tags)}개, 출력 {len(result)}개")


def main():
    client = boto3.client("bedrock-runtime", region_name=REGION)

    en_kb = json.loads(INPUT_EN_PATH.read_text(encoding="utf-8"))
    ko_kb = json.loads(INPUT_KO_PATH.read_text(encoding="utf-8"))

    en_metadatas = en_kb["metadatas"]
    en_ids = en_kb["ids"]
    ko_documents = ko_kb["documents"]
    total = len(en_metadatas)

    assert total == len(ko_documents), "문서 수 불일치!"
    print(f"총 {total}개 메타데이터 처리 시작")

    # ── extracted_tags 번역 ──────────────────────────────────────────────────
    all_tags = [m["extracted_tags"] for m in en_metadatas]
    translated_tags: dict[int, str] = load_checkpoint()

    if translated_tags:
        print(f"체크포인트 재개: {len(translated_tags)}개 완료")

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_indices = list(range(batch_start, batch_end))

        if all(i in translated_tags for i in batch_indices):
            print(f"[{batch_start+1:3d}-{batch_end:3d}] 스킵")
            continue

        batch = [all_tags[i] for i in batch_indices]
        print(f"[{batch_start+1:3d}-{batch_end:3d}] 번역 중...", end=" ", flush=True)

        try:
            results = translate_tags_batch(batch, client)
            for i, text in zip(batch_indices, results):
                translated_tags[i] = text
            save_checkpoint(translated_tags)
            print(f"완료 ({len(translated_tags)}/{total})")
        except Exception as e:
            print(f"실패: {e} → 원문 유지")
            for i, tag in zip(batch_indices, batch):
                translated_tags[i] = tag
            save_checkpoint(translated_tags)

        time.sleep(0.3)

    # ── 최종 메타데이터 조합 ─────────────────────────────────────────────────
    metadatas_ko = []
    for i, meta in enumerate(en_metadatas):
        m = dict(meta)
        m["language"] = "ko"
        m["base_nutrient"] = BASE_NUTRIENT_MAP.get(m["base_nutrient"], m["base_nutrient"])
        m["interaction_type"] = INTERACTION_TYPE_MAP.get(m["interaction_type"], m["interaction_type"])
        m["extracted_tags"] = translated_tags[i]
        metadatas_ko.append(m)

    # ── 저장 ────────────────────────────────────────────────────────────────
    output = {
        "documents": ko_documents,
        "metadatas": metadatas_ko,
        "ids": en_ids,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료! 저장됨: {OUTPUT_PATH}")
    CHECKPOINT_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
