"""
타로 데이터 검증 스크립트
Usage: python validate.py [--cards] [--generated]

Phase 0: 마스터 카드 데이터 검증
Phase 1+: 생성된 텍스트 검증
"""

import json
import re
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_PATH = BASE_DIR / "data" / "cards" / "major_arcana.json"
GENERATED_DIR = BASE_DIR / "data" / "generated"

REQUIRED_CARD_FIELDS = ["id", "name_kr", "name_en", "number", "arcana", "attributes", "keywords", "core_meaning", "situation_snippets", "combination_tags"]


def validate_cards():
    """마스터 카드 데이터 검증"""
    print("=== 마스터 카드 데이터 검증 ===\n")
    errors = []

    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        cards = json.load(f)

    # 총 22장 확인
    if len(cards) != 22:
        errors.append(f"카드 수: {len(cards)}장 (22장이어야 함)")

    seen_ids = set()
    seen_numbers = set()

    for card in cards:
        cid = card.get("id", "UNKNOWN")

        # 필수 필드
        for field in REQUIRED_CARD_FIELDS:
            if field not in card:
                errors.append(f"[{cid}] 필수 필드 누락: {field}")

        # 중복 ID
        if cid in seen_ids:
            errors.append(f"[{cid}] 중복 ID")
        seen_ids.add(cid)

        # 중복 번호
        num = card.get("number")
        if num in seen_numbers:
            errors.append(f"[{cid}] 중복 번호: {num}")
        seen_numbers.add(num)

        # 키워드 최소 3개
        for direction in ["upright", "reversed"]:
            kw = card.get("keywords", {}).get(direction, [])
            if len(kw) < 3:
                errors.append(f"[{cid}] {direction} 키워드 {len(kw)}개 (최소 3개)")

        # 핵심 의미 완결성
        for direction in ["upright", "reversed"]:
            meaning = card.get("core_meaning", {}).get(direction, "")
            if len(meaning) < 10:
                errors.append(f"[{cid}] {direction} 핵심 의미 너무 짧음: {len(meaning)}자")

        # 상황 스니펫
        for sit in ["love", "career", "finance"]:
            snippet = card.get("situation_snippets", {}).get(sit, "")
            if len(snippet) < 5:
                errors.append(f"[{cid}] {sit} 스니펫 너무 짧음: {len(snippet)}자")

        # combination_tags 최소 3개
        tags = card.get("combination_tags", [])
        if len(tags) < 3:
            errors.append(f"[{cid}] combination_tags {len(tags)}개 (최소 3개)")

    # 0~21 번호 전체 존재
    expected = set(range(22))
    if seen_numbers != expected:
        missing = expected - seen_numbers
        errors.append(f"누락된 번호: {missing}")

    if errors:
        print(f"❌ {len(errors)}개 오류 발견:\n")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print(f"✅ {len(cards)}장 검증 통과!")
        return True


def validate_generated():
    """생성된 텍스트 검증"""
    print("\n=== 생성 텍스트 검증 ===\n")

    if not GENERATED_DIR.exists():
        print("생성 디렉토리가 없습니다.")
        return False

    files = list(GENERATED_DIR.glob("worker_*.json"))
    if not files:
        print("생성된 파일이 없습니다.")
        return False

    total = 0
    errors = []
    all_results = {}

    for fp in sorted(files):
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)

        for key, result in data.items():
            total += 1
            all_results[key] = result

            for pos in ["past", "present", "future"]:
                text = result.get(pos, "")
                length = len(text)
                if length < 70:
                    errors.append(f"[{key}] {pos}: {length}자 (최소 70자)")
                if length > 130:
                    errors.append(f"[{key}] {pos}: {length}자 (최대 130자)")
                if re.search(r'[a-zA-Z]{2,}', text):
                    errors.append(f"[{key}] {pos}: 영어 포함")

            synergy = result.get("synergy", "")
            if len(synergy) < 20:
                errors.append(f"[{key}] synergy: {len(synergy)}자 (최소 20자)")

    print(f"총 {total}개 결과 검사")
    print(f"파일 수: {len(files)}")

    if errors:
        print(f"\n⚠ {len(errors)}개 경고:")
        for e in errors[:20]:
            print(f"  - {e}")
        if len(errors) > 20:
            print(f"  ... 외 {len(errors) - 20}개")
    else:
        print("✅ 모든 생성 텍스트 검증 통과!")

    return len(errors) == 0


if __name__ == "__main__":
    ok = True
    if "--cards" in sys.argv or len(sys.argv) == 1:
        ok = validate_cards() and ok
    if "--generated" in sys.argv or len(sys.argv) == 1:
        ok = validate_generated() and ok

    sys.exit(0 if ok else 1)
