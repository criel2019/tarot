"""
타로 카드 해석 텍스트 배치 생성 스크립트
Usage:
  python generate.py --worker-id 0 --total-workers 4
  python generate.py --worker-id 0 --total-workers 1 --pilot  # Phase 0.5 (100건 파일럿)

Claude Sonnet을 사용하여 3장 조합별 해석 텍스트를 생성합니다.
각 워커는 독립적으로 실행 가능하며 체크포인트 기반으로 중단/재개됩니다.
"""

import argparse
import json
import os
import sys
import io
import time
import random
import hashlib
from pathlib import Path
from itertools import permutations

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── 설정 ──────────────────────────────────────────
MODEL = "claude-sonnet-4-5-20250514"
BATCH_SIZE = 5          # 한 API 호출당 조합 수
MAX_RETRIES = 3         # 재시도 횟수
CHAR_MIN = 70           # 최소 글자 수
CHAR_MAX = 130          # 최대 글자 수
SYNERGY_MIN = 20        # 시너지 최소 글자
SYNERGY_MAX = 50        # 시너지 최대 글자

BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_PATH = BASE_DIR / "data" / "cards" / "major_arcana.json"
GENERATED_DIR = BASE_DIR / "data" / "generated"
CHECKPOINT_DIR = BASE_DIR / "data" / "checkpoint"

SYSTEM_PROMPT = """당신은 따뜻하고 통찰력 있는 한국어 타로 리더입니다.

## 규칙
- 톤: 친근하고 실용적. 과도한 신비주의 지양.
- 각 해석 텍스트: 반드시 70~130자 (한글 기준)
- 시너지 코멘트: 20~50자 한 줄
- 금지: 영어 혼용, "~것입니다" 3회 이상 반복, 카드 이름 직접 언급
- 필수: 두 번째 문장은 행동 지침을 포함할 것
- 각 해석은 해당 포지션(과거/현재/미래)의 시간적 맥락을 반영할 것

## 문체 시드
- seed 1: 경어체 (~합니다)
- seed 2: 해요체 (~해요)
- seed 3: 질문형 (~하지 않을까요?)
- seed 4: 비유형 (은유와 비유 활용)
- seed 5: 직설체 (~이다)

## 출력 형식
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.
여러 조합이 주어지면 JSON 배열로 응답하세요.

단일 조합:
{"past":"과거 해석","present":"현재 해석","future":"미래 해석","synergy":"시너지 한줄 코멘트"}

복수 조합:
[{"past":"...","present":"...","future":"...","synergy":"..."}, ...]"""


def load_cards():
    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        cards = json.load(f)
    return {c["id"]: c for c in cards}


def generate_combinations(cards_dict):
    """22P3 = 9240개의 고유 3장 순서 조합 생성"""
    card_ids = sorted(cards_dict.keys())
    combos = []
    for perm in permutations(card_ids, 3):
        combos.append(perm)
    return combos


def get_worker_slice(combos, worker_id, total_workers):
    """워커별 담당 조합 분배"""
    return [c for i, c in enumerate(combos) if i % total_workers == worker_id]


def build_user_prompt(batch, cards_dict):
    """배치 내 조합들에 대한 유저 프롬프트 생성"""
    lines = []
    for idx, (c1, c2, c3) in enumerate(batch):
        seed = random.randint(1, 5)
        card1 = cards_dict[c1]
        card2 = cards_dict[c2]
        card3 = cards_dict[c3]

        lines.append(f"[조합 {idx + 1}] seed:{seed}")
        lines.append(f"  과거: {card1['name_kr']}(정방향) — 키워드: {', '.join(card1['keywords']['upright'][:3])}")
        lines.append(f"  현재: {card2['name_kr']}(정방향) — 키워드: {', '.join(card2['keywords']['upright'][:3])}")
        lines.append(f"  미래: {card3['name_kr']}(정방향) — 키워드: {', '.join(card3['keywords']['upright'][:3])}")
        tags = set(card1["combination_tags"] + card2["combination_tags"] + card3["combination_tags"])
        lines.append(f"  시너지 태그: {', '.join(list(tags)[:6])}")
        lines.append("")

    if len(batch) == 1:
        lines.append("위 조합에 대해 JSON 객체 하나를 생성하세요.")
    else:
        lines.append(f"위 {len(batch)}개 조합에 대해 JSON 배열을 생성하세요.")

    return "\n".join(lines)


def combo_key(c1, c2, c3):
    """조합 고유 키"""
    return f"{c1}_{c2}_{c3}_ur"


def load_checkpoint(worker_id):
    """체크포인트 로드"""
    cp_path = CHECKPOINT_DIR / f"checkpoint_worker_{worker_id}.json"
    if cp_path.exists():
        with open(cp_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "worker_id": worker_id,
        "completed": [],
        "failed": [],
        "stats": {
            "total_generated": 0,
            "total_api_calls": 0,
            "quality_pass": 0,
            "quality_fail": 0,
            "total_time_ms": 0
        }
    }


def save_checkpoint(checkpoint, worker_id):
    """체크포인트 저장"""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp_path = CHECKPOINT_DIR / f"checkpoint_worker_{worker_id}.json"
    with open(cp_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


def validate_result(result):
    """품질 필터 (하드 컷)"""
    errors = []
    for pos in ["past", "present", "future"]:
        text = result.get(pos, "")
        length = len(text)
        if length < CHAR_MIN:
            errors.append(f"{pos}: {length}자 (최소 {CHAR_MIN}자)")
        if length > CHAR_MAX:
            errors.append(f"{pos}: {length}자 (최대 {CHAR_MAX}자)")
        if not text.endswith((".", "요.", "다.", "요", "다", "세요.", "까요?", "세요", "니다.", "니다")):
            pass  # 유연하게 처리
        import re
        if re.search(r'[a-zA-Z]{2,}', text):
            errors.append(f"{pos}: 영어 단어 포함")

    synergy = result.get("synergy", "")
    if len(synergy) < SYNERGY_MIN:
        errors.append(f"synergy: {len(synergy)}자 (최소 {SYNERGY_MIN}자)")
    if len(synergy) > SYNERGY_MAX:
        errors.append(f"synergy: {len(synergy)}자 (최대 {SYNERGY_MAX}자)")

    return errors


def save_results(results_dict, worker_id, batch_num):
    """생성 결과 저장"""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GENERATED_DIR / f"worker_{worker_id}_batch_{batch_num:04d}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results_dict, f, ensure_ascii=False, indent=2)


def call_api(client, user_prompt):
    """Anthropic API 호출"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.9
    )
    return response.content[0].text


def parse_response(text, batch_size):
    """API 응답 JSON 파싱"""
    text = text.strip()
    # 코드블록 제거
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()

    parsed = json.loads(text)
    if batch_size == 1 and isinstance(parsed, dict):
        return [parsed]
    return parsed


def main():
    parser = argparse.ArgumentParser(description="타로 해석 텍스트 배치 생성")
    parser.add_argument("--worker-id", type=int, default=0, help="워커 ID (0부터)")
    parser.add_argument("--total-workers", type=int, default=1, help="총 워커 수")
    parser.add_argument("--pilot", action="store_true", help="파일럿 모드 (100건만)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="배치 크기")
    parser.add_argument("--api-key", type=str, default=None, help="Anthropic API 키")
    args = parser.parse_args()

    # API 키
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY 환경변수를 설정하거나 --api-key를 전달하세요.")
        sys.exit(1)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    # 카드 데이터 로드
    cards_dict = load_cards()
    print(f"[Worker {args.worker_id}] 카드 {len(cards_dict)}장 로드 완료")

    # 조합 생성 및 분배
    all_combos = generate_combinations(cards_dict)
    my_combos = get_worker_slice(all_combos, args.worker_id, args.total_workers)
    print(f"[Worker {args.worker_id}] 전체 {len(all_combos)}개 중 {len(my_combos)}개 담당")

    # 파일럿 모드
    if args.pilot:
        random.seed(42)
        random.shuffle(my_combos)
        my_combos = my_combos[:100]
        print(f"[Worker {args.worker_id}] 파일럿 모드: 100건만 생성")

    # 체크포인트 로드
    checkpoint = load_checkpoint(args.worker_id)
    completed_set = set(checkpoint["completed"])
    remaining = [c for c in my_combos if combo_key(*c) not in completed_set]
    print(f"[Worker {args.worker_id}] 완료: {len(completed_set)}, 남은: {len(remaining)}")

    if not remaining:
        print(f"[Worker {args.worker_id}] 모든 작업 완료!")
        return

    # 배치 처리
    batch_num = checkpoint["stats"]["total_api_calls"]
    total_remaining = len(remaining)
    batch_size = args.batch_size

    for i in range(0, total_remaining, batch_size):
        batch = remaining[i:i + batch_size]
        batch_num += 1

        print(f"\n[Worker {args.worker_id}] 배치 {batch_num} ({i + 1}~{min(i + batch_size, total_remaining)}/{total_remaining})")

        # 프롬프트 생성
        user_prompt = build_user_prompt(batch, cards_dict)

        # API 호출 (재시도 포함)
        result_text = None
        for retry in range(MAX_RETRIES):
            try:
                start = time.time()
                result_text = call_api(client, user_prompt)
                elapsed = int((time.time() - start) * 1000)
                checkpoint["stats"]["total_time_ms"] += elapsed
                checkpoint["stats"]["total_api_calls"] += 1
                break
            except Exception as e:
                print(f"  API 오류 (시도 {retry + 1}/{MAX_RETRIES}): {e}")
                if retry < MAX_RETRIES - 1:
                    time.sleep(2 ** retry)

        if result_text is None:
            for c in batch:
                checkpoint["failed"].append(combo_key(*c))
            save_checkpoint(checkpoint, args.worker_id)
            continue

        # 응답 파싱
        try:
            results = parse_response(result_text, len(batch))
        except json.JSONDecodeError as e:
            print(f"  JSON 파싱 실패: {e}")
            for c in batch:
                checkpoint["failed"].append(combo_key(*c))
            save_checkpoint(checkpoint, args.worker_id)
            continue

        # 품질 검증 및 저장
        batch_results = {}
        for idx, (combo, result) in enumerate(zip(batch, results)):
            key = combo_key(*combo)
            errors = validate_result(result)
            if errors:
                print(f"  ⚠ {key}: {', '.join(errors)}")
                checkpoint["stats"]["quality_fail"] += 1
                checkpoint["failed"].append(key)
            else:
                batch_results[key] = result
                checkpoint["completed"].append(key)
                checkpoint["stats"]["quality_pass"] += 1
                checkpoint["stats"]["total_generated"] += 1

        if batch_results:
            save_results(batch_results, args.worker_id, batch_num)

        save_checkpoint(checkpoint, args.worker_id)

        # 진행 상황 출력
        done = checkpoint["stats"]["total_generated"]
        total = len(my_combos)
        pct = done / total * 100 if total else 0
        avg_ms = checkpoint["stats"]["total_time_ms"] / max(checkpoint["stats"]["total_api_calls"], 1)
        print(f"  ✓ 진행: {done}/{total} ({pct:.1f}%) | 평균 {avg_ms:.0f}ms/호출")

    # 최종 통계
    stats = checkpoint["stats"]
    print(f"\n{'=' * 50}")
    print(f"[Worker {args.worker_id}] 완료!")
    print(f"  생성: {stats['total_generated']}건")
    print(f"  실패: {stats['quality_fail']}건")
    print(f"  API 호출: {stats['total_api_calls']}회")
    if stats['total_api_calls'] > 0:
        print(f"  평균 속도: {stats['total_time_ms'] / stats['total_api_calls']:.0f}ms/호출")
    print(f"  통과율: {stats['quality_pass'] / max(stats['quality_pass'] + stats['quality_fail'], 1) * 100:.1f}%")


if __name__ == "__main__":
    main()
