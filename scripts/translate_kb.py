"""
KB 문서 번역 스크립트 (영어 → 한국어)
사용법: python scripts/translate_kb.py
결과: lpi_kb_texts_ko.json

- claude-sonnet-4-6 사용
- 중단 시 checkpoint 자동 재개
- 배치 처리 (10개씩)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3

REGION = "ap-northeast-2"
MODEL_ID = "global.anthropic.claude-sonnet-4-6"
BATCH_SIZE = 10
CHECKPOINT_PATH = Path("scripts/translate_checkpoint.json")
OUTPUT_PATH = Path("lpi_kb_texts_ko.json")
INPUT_PATH = Path("lpi_kb_texts.json")

TRANSLATE_PROMPT = """아래 영어 영양학/약학 문서들을 한국어로 번역하세요.

번역 규칙:
- 의학/영양학 전문 용어는 한국어로 번역하되 괄호 안에 영문 원어를 표기하세요
  예: 코엔자임 Q10 (Coenzyme Q10), 스타틴 (Statin), 메트포르민 (Metformin)
- 약품명(고유명사)은 한국어 발음 표기 + 괄호 영문
  예: 아토르바스타틴 (atorvastatin), 와파린 (warfarin)
- 수치/단위는 그대로 유지 (mg, mcg, IU, ng/mL 등)
- 문장 구조는 자연스러운 한국어로

입력 형식: JSON 배열 (각 원소가 문서 1개)
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


def translate_batch(docs: list[str], start_idx: int, client) -> list[str]:
    texts_json = json.dumps(docs, ensure_ascii=False, indent=2)
    prompt = TRANSLATE_PROMPT.format(texts=texts_json)

    raw = call_bedrock(prompt, client)

    # JSON 파싱
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```")[0]

    try:
        result = json.loads(raw.strip())
        if isinstance(result, list) and len(result) == len(docs):
            return result
        raise ValueError(f"길이 불일치: 입력 {len(docs)}개, 출력 {len(result)}개")
    except Exception as e:
        print(f"  [파싱 오류] {e}")
        print(f"  원문 앞 200자: {raw[:200]}")
        raise


def main():
    client = boto3.client("bedrock-runtime", region_name=REGION)

    kb = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    docs = kb["documents"]
    total = len(docs)
    print(f"총 {total}개 문서 번역 시작 (배치 크기: {BATCH_SIZE})")

    # 체크포인트 로드
    translated: dict[int, str] = load_checkpoint()
    if translated:
        print(f"체크포인트 재개: {len(translated)}개 완료")

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_indices = list(range(batch_start, batch_end))

        # 이미 번역된 배치 스킵
        if all(i in translated for i in batch_indices):
            print(f"[{batch_start+1:3d}-{batch_end:3d}] 스킵 (이미 완료)")
            continue

        batch_docs = [docs[i] for i in batch_indices]
        print(f"[{batch_start+1:3d}-{batch_end:3d}] 번역 중...", end=" ", flush=True)

        try:
            results = translate_batch(batch_docs, batch_start, client)
            for i, text in zip(batch_indices, results):
                translated[i] = text
            save_checkpoint(translated)
            print(f"완료 ({len(translated)}/{total})")
        except Exception as e:
            print(f"실패: {e}")
            print("  → 해당 배치 원문 유지 후 계속")
            for i, doc in zip(batch_indices, batch_docs):
                translated[i] = doc  # 원문 fallback
            save_checkpoint(translated)

        time.sleep(0.5)

    # 최종 출력
    output_docs = [translated[i] for i in range(total)]
    output = {"documents": output_docs}
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n번역 완료! 저장됨: {OUTPUT_PATH}")
    print(f"체크포인트 삭제: {CHECKPOINT_PATH}")
    CHECKPOINT_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
