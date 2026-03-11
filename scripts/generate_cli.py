"""
타로 카드 해석 텍스트 생성 (Claude CLI 기반 — $0)
Usage:
  python generate_cli.py --worker-id 0 --total-workers 4
  python generate_cli.py --worker-id 0 --total-workers 1 --pilot
  python generate_cli.py --worker-id 0 --total-workers 1 --count 200

Claude Code Max 구독의 claude CLI를 사용하여 비용 $0으로 생성합니다.
각 워커는 독립 프로세스로 병렬 실행 가능합니다.
"""

import argparse
import json
import os
import sys
import io
import time
import random
import subprocess
import re
from pathlib import Path
from itertools import permutations

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── 설정 ──
BATCH_SIZE = 3           # CLI 호출 당 조합 수 (CLI는 API보다 느리므로 작게)
CHAR_MIN = 70
CHAR_MAX = 130
SYNERGY_MIN = 15
SYNERGY_MAX = 55

BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_PATH = BASE_DIR / "data" / "cards" / "major_arcana.json"
GENERATED_DIR = BASE_DIR / "data" / "generated"
CHECKPOINT_DIR = BASE_DIR / "data" / "checkpoint"

SYSTEM_PROMPT = """당신은 따뜻하고 통찰력 있는 한국어 타로 리더입니다.

규칙:
- 톤: 친근하고 실용적. 과도한 신비주의 지양.
- 각 해석: 반드시 70~130자(한글 기준). 두 번째 문장은 행동 지침 포함.
- 시너지 코멘트: 15~55자 한 줄.
- 금지: 영어 혼용, 카드 이름 직접 언급, "~것입니다" 3회 이상.
- 각 해석은 해당 포지션(과거/현재/미래)의 시간적 맥락 반영.

문체 시드: 1=경어체, 2=해요체, 3=질문형, 4=비유형, 5=직설체

반드시 JSON으로만 응답. 단일: {"past":"...","present":"...","future":"...","synergy":"..."}
복수: [{"past":"...","present":"...","future":"...","synergy":"..."}, ...]"""


def load_cards():
    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        return {c["id"]: c for c in json.load(f)}


def generate_combinations(cards_dict):
    card_ids = sorted(cards_dict.keys())
    return list(permutations(card_ids, 3))


def get_worker_slice(combos, worker_id, total_workers):
    return [c for i, c in enumerate(combos) if i % total_workers == worker_id]


def combo_key(c1, c2, c3):
    return f"{c1}_{c2}_{c3}_ur"


def build_prompt(batch, cards_dict):
    lines = [SYSTEM_PROMPT, "", "---", ""]
    for idx, (c1, c2, c3) in enumerate(batch):
        seed = random.randint(1, 5)
        card1, card2, card3 = cards_dict[c1], cards_dict[c2], cards_dict[c3]
        lines.append(f"[조합 {idx+1}] seed:{seed}")
        lines.append(f"  과거: {card1['name_kr']}(정) - {', '.join(card1['keywords']['upright'][:3])}")
        lines.append(f"  현재: {card2['name_kr']}(정) - {', '.join(card2['keywords']['upright'][:3])}")
        lines.append(f"  미래: {card3['name_kr']}(정) - {', '.join(card3['keywords']['upright'][:3])}")
        tags = list(set(card1["combination_tags"] + card2["combination_tags"] + card3["combination_tags"]))[:6]
        lines.append(f"  시너지 태그: {', '.join(tags)}")
        lines.append("")

    if len(batch) == 1:
        lines.append("위 조합에 대해 JSON 객체 하나를 생성하세요.")
    else:
        lines.append(f"위 {len(batch)}개 조합에 대해 JSON 배열을 생성하세요.")
    return "\n".join(lines)


def call_claude_cli(prompt):
    """claude CLI를 사용하여 텍스트 생성 (Claude Code Max = $0)"""
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "sonnet", "--output-format", "text"],
        capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"CLI error: {result.stderr}")
    return result.stdout.strip()


def parse_response(text, batch_size):
    text = text.strip()
    # 코드블록 제거
    if "```" in text:
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            text = match.group(1).strip()
    parsed = json.loads(text)
    if batch_size == 1 and isinstance(parsed, dict):
        return [parsed]
    return parsed


def validate_result(result):
    errors = []
    for pos in ["past", "present", "future"]:
        text = result.get(pos, "")
        if len(text) < CHAR_MIN:
            errors.append(f"{pos}: {len(text)}자 < {CHAR_MIN}")
        if len(text) > CHAR_MAX:
            errors.append(f"{pos}: {len(text)}자 > {CHAR_MAX}")
        if re.search(r'[a-zA-Z]{2,}', text):
            errors.append(f"{pos}: 영어 포함")
    synergy = result.get("synergy", "")
    if len(synergy) < SYNERGY_MIN:
        errors.append(f"synergy: {len(synergy)}자 < {SYNERGY_MIN}")
    if len(synergy) > SYNERGY_MAX:
        errors.append(f"synergy: {len(synergy)}자 > {SYNERGY_MAX}")
    return errors


def load_checkpoint(worker_id):
    cp_path = CHECKPOINT_DIR / f"checkpoint_worker_{worker_id}.json"
    if cp_path.exists():
        with open(cp_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"worker_id": worker_id, "completed": [], "failed": [],
            "stats": {"total_generated": 0, "total_calls": 0, "quality_pass": 0, "quality_fail": 0}}


def save_checkpoint(cp, worker_id):
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_DIR / f"checkpoint_worker_{worker_id}.json", "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=2)


def save_results(data, worker_id, batch_num):
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    with open(GENERATED_DIR / f"worker_{worker_id}_batch_{batch_num:04d}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="타로 해석 생성 (Claude CLI)")
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--total-workers", type=int, default=1)
    parser.add_argument("--pilot", action="store_true", help="파일럿 모드 (100건)")
    parser.add_argument("--count", type=int, default=0, help="생성할 개수 (0=전체)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    cards_dict = load_cards()
    print(f"[Worker {args.worker_id}] 카드 {len(cards_dict)}장 로드")

    all_combos = generate_combinations(cards_dict)
    my_combos = get_worker_slice(all_combos, args.worker_id, args.total_workers)
    print(f"[Worker {args.worker_id}] 전체 {len(all_combos)}개 중 {len(my_combos)}개 담당")

    if args.pilot:
        random.seed(42)
        random.shuffle(my_combos)
        my_combos = my_combos[:100]
        print(f"[Worker {args.worker_id}] 파일럿 모드: 100건")
    elif args.count > 0:
        random.seed(42 + args.worker_id)
        random.shuffle(my_combos)
        my_combos = my_combos[:args.count]
        print(f"[Worker {args.worker_id}] {args.count}건 제한")

    cp = load_checkpoint(args.worker_id)
    completed_set = set(cp["completed"])
    remaining = [c for c in my_combos if combo_key(*c) not in completed_set]
    print(f"[Worker {args.worker_id}] 완료: {len(completed_set)}, 남은: {len(remaining)}")

    if not remaining:
        print("모든 작업 완료!")
        return

    batch_num = cp["stats"]["total_calls"]
    total = len(remaining)
    bs = args.batch_size

    for i in range(0, total, bs):
        batch = remaining[i:i+bs]
        batch_num += 1
        print(f"\n[배치 {batch_num}] {i+1}~{min(i+bs, total)}/{total}")

        prompt = build_prompt(batch, cards_dict)

        try:
            start = time.time()
            raw = call_claude_cli(prompt)
            elapsed = time.time() - start
            cp["stats"]["total_calls"] += 1
            print(f"  응답 수신 ({elapsed:.1f}s)")
        except Exception as e:
            print(f"  오류: {e}")
            for c in batch:
                cp["failed"].append(combo_key(*c))
            save_checkpoint(cp, args.worker_id)
            continue

        try:
            results = parse_response(raw, len(batch))
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {e}")
            print(f"  원본: {raw[:200]}")
            for c in batch:
                cp["failed"].append(combo_key(*c))
            save_checkpoint(cp, args.worker_id)
            continue

        batch_data = {}
        for combo, result in zip(batch, results):
            key = combo_key(*combo)
            errors = validate_result(result)
            if errors:
                print(f"  경고 {key}: {', '.join(errors)}")
                cp["stats"]["quality_fail"] += 1
                # 소프트 경고 — 저장은 함 (나중에 재생성 가능)
                batch_data[key] = result
                cp["completed"].append(key)
                cp["stats"]["total_generated"] += 1
            else:
                batch_data[key] = result
                cp["completed"].append(key)
                cp["stats"]["quality_pass"] += 1
                cp["stats"]["total_generated"] += 1

        if batch_data:
            save_results(batch_data, args.worker_id, batch_num)

        save_checkpoint(cp, args.worker_id)
        done = cp["stats"]["total_generated"]
        pct = done / len(my_combos) * 100
        print(f"  진행: {done}/{len(my_combos)} ({pct:.1f}%)")

    s = cp["stats"]
    print(f"\n{'='*40}")
    print(f"[Worker {args.worker_id}] 완료!")
    print(f"  생성: {s['total_generated']}건 | 통과: {s['quality_pass']} | 경고: {s['quality_fail']}")


if __name__ == "__main__":
    main()
