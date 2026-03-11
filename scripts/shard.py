"""
버킷 샤딩 스크립트
Usage: python shard.py

생성된 텍스트를 첫 번째 카드 기준으로 22개 버킷 파일로 분할합니다.
index.json도 함께 생성합니다.
"""

import json
import hashlib
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
GENERATED_DIR = BASE_DIR / "data" / "generated"
BUCKETS_DIR = BASE_DIR / "web" / "buckets"
INDEX_PATH = BASE_DIR / "web" / "index.json"
CARDS_PATH = BASE_DIR / "data" / "cards" / "major_arcana.json"


def content_hash(data_str):
    return hashlib.md5(data_str.encode()).hexdigest()[:8]


def main():
    print("=== 버킷 샤딩 시작 ===\n")

    # 카드 데이터 로드
    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        cards = json.load(f)
    card_names = {c["id"]: c["name_kr"] for c in cards}

    # 모든 생성 결과 수집
    all_results = {}
    files = list(GENERATED_DIR.glob("worker_*.json"))
    for fp in sorted(files):
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_results.update(data)

    print(f"총 {len(all_results)}개 결과 로드")

    # 첫 번째 카드 기준으로 버킷 분배
    buckets = {}
    for key, result in all_results.items():
        parts = key.split("_")
        first_card = f"{parts[0]}_{parts[1]}"  # e.g., "major_00"
        if first_card not in buckets:
            buckets[first_card] = {}
        buckets[first_card][key] = result

    # 버킷 파일 생성
    BUCKETS_DIR.mkdir(parents=True, exist_ok=True)
    index = {"version": "2.0", "buckets": {}, "total": len(all_results)}

    for card_id in sorted(buckets.keys()):
        bucket_data = buckets[card_id]
        data_str = json.dumps(bucket_data, ensure_ascii=False, sort_keys=True)
        h = content_hash(data_str)
        filename = f"{card_id}_bucket_{h}.json"

        out_path = BUCKETS_DIR / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(bucket_data, f, ensure_ascii=False)

        name = card_names.get(card_id, card_id)
        index["buckets"][card_id] = {
            "file": filename,
            "count": len(bucket_data),
            "name": name
        }
        print(f"  {name} ({card_id}): {len(bucket_data)}개 → {filename}")

    # index.json 생성
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {len(buckets)}개 버킷 생성 완료")
    print(f"   총 {len(all_results)}개 해석 텍스트")
    print(f"   index.json 생성 완료")

    # 용량 확인
    total_bytes = sum(f.stat().st_size for f in BUCKETS_DIR.glob("*.json"))
    print(f"   총 용량: {total_bytes / 1024:.1f} KB")


if __name__ == "__main__":
    main()
