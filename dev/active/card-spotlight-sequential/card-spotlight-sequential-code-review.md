# 코드 리뷰: 카드별 즉시 리딩 + 스포트라이트 카드 표시

Last Updated: 2026-03-12

## 개요

커밋 `e8fac1d` — "feat: 카드별 즉시 리딩 + 스포트라이트 카드 표시"

변경 대상 파일: `index.html` (단일 파일 프로젝트)

이번 커밋은 타로 리딩 UX 흐름을 전면 개편한 작업이다. 이전에는 "모든 카드를 뒤집은 뒤 일괄 리딩"하는 방식이었고, 이번 변경으로 "카드를 순차적으로 하나씩 뒤집을 때마다 즉시 스포트라이트 카드를 보여주고 해당 카드 리딩을 바로 전달"하는 방식으로 바뀌었다.

---

## Executive Summary

핵심 UX 개선 방향은 타당하다. 카드를 하나씩 뒤집을 때마다 즉각적인 피드백을 주는 방식은 사용자 몰입도를 높인다. 그러나 구현 과정에서 몇 가지 설계상 취약점이 발생했다. 가장 심각한 문제는 `dlgCallback` 전역 변수를 중첩 클로저로 재사용하는 패턴으로, 이로 인해 비선형적 인터랙션(빠른 클릭, 리플레이 중 연타)에서 상태 불일치가 발생할 수 있다. 그 외 CSS 중복, innerHTML을 통한 XSS 잠재 위험, `today`/`general` 토픽 키 매핑 누락 버그 등이 있다.

---

## Critical Issues (반드시 수정)

### 1. dlgCallback 중첩 재할당 패턴 — 상태 경쟁 조건

**위치**: `index.html` 1197~1243번 라인, `playReactionAndRead()` 함수

**문제**:

```javascript
// After reaction dialogue, show spotlight and deliver this card's reading
dlgCallback = function() {
    $cardArea.classList.remove('show');
    showCardSpotlight(card);
    cardReading.lines.forEach(function(line) {
      say(ch, line);
    });

    // After reading, hide spotlight and proceed
    dlgCallback = function() {   // <-- 내부에서 dlgCallback을 다시 덮어씀
      hideCardSpotlight();
      if (flippedCount < drawnCards.length) {
        ...
        dlgCallback = function() { hideDlg(); };  // <-- 또 덮어씀
      } else {
        ...
      }
    };
};
```

`dlgCallback`은 단일 전역 변수다. 사용자가 대화 상자를 빠르게 연속 클릭하거나, 쓰리카드 스프레드에서 첫 번째 카드 리딩이 진행되는 도중 두 번째 카드를 뒤집으면 (card-locked이 해제된 시점에서) `dlgCallback`이 충돌한다.

구체적으로:
- 첫 번째 카드 반응 대사 중 두 번째 카드가 클릭되면 (UI상으론 잠겨있지만, 타이밍 경쟁 조건이 존재), `flippedCount`가 선행 갱신되고 `playReactionAndRead`가 다시 호출되어 `dlgCallback`을 덮어쓴다.
- 결국 첫 번째 카드의 리딩 콜백이 두 번째 카드의 반응 콜백에 의해 소실된다.

**원인 분석**: 이전 구현의 `startReading()` → `deliverCardReading(0)` 재귀 패턴은 cardIdx를 인자로 받아 상태를 완전히 캡슐화했다. 새 구현은 이 상태를 클로저와 전역 변수 사이에 분산시켰다.

**권장 수정 방향**: 콜백 스택(배열)을 도입하거나, 현재 처리 중인 cardIdx를 별도 변수로 관리하여 카드 잠금 로직을 더 엄격하게 적용해야 한다. 예를 들어 `isReadingInProgress` 플래그를 두어 리딩이 진행 중일 때는 모든 카드 클릭을 차단하는 것이 가장 간단한 해결책이다.

---

### 2. storedReadings 조기 생성 — 카드 드로우 타이밍 불일치

**위치**: `onSpreadPicked()` 함수 1769~1774번 라인

```javascript
dlgCallback=function(){
    hideDlg();
    flippedCount=0;
    drawCards(selectedSpread);
    storedReadings = generateAllReadings();  // <-- 추가된 라인
    showCards();
};
```

**문제**: `generateAllReadings()`는 `drawnCards` 배열을 순회하여 각 카드의 `lines`를 생성한다. 이 시점에는 카드가 방금 드로우되었으므로 데이터 자체는 맞다. 그러나 이전 구현에서 `startReading()` 내부에서 생성하던 것을 앞당긴 이유가 명확하지 않으며, 부작용이 있다.

`generateAllReadings()` 안에서 `drawnCards`의 순서와 내용에 의존하는데, `drawCards()`와 `generateAllReadings()` 사이에 아무런 검증이 없다. 만약 `cardData`가 빈 배열인 경우(로드 실패 시) `drawnCards`도 비어 있고 `storedReadings.cards`도 비어있게 된다. 이후 `playReactionAndRead(card, idx)` 내부에서 `storedReadings.cards[idx]`에 접근할 때 `undefined`가 반환되어 `cardReading.lines.forEach` 호출 시 런타임 에러가 발생한다.

이전 구현에서 `startReading()` 시점에 생성하면 에러 발생 지점이 다르지만, 새 구현에서는 에러 방어 코드가 없다.

**권장 수정**:
```javascript
var cardReading = storedReadings && storedReadings.cards[idx];
if (!cardReading) return;  // 방어 처리 추가
```

---

### 3. innerHTML에 카드 데이터 직접 삽입 — XSS 잠재 위험

**위치**: `showCardSpotlight()` 함수 1168~1176번 라인, `showCards()` 내 `front.innerHTML` 1138~1144번 라인

```javascript
spot.innerHTML =
    '<div class="spotlight-card'+(isR?' reversed':'')+'">' +
    '<div class="card-numeral">'+toRoman(card.number)+'</div>'+
    '<div class="card-name-en">'+card.name_en+'</div>'+  // JSON에서 온 값
    ...
```

`card.name_en`, `card.name_kr` 등의 값이 외부 JSON(`data/cards/major_arcana.json`)에서 로드된다. 현재는 개발자가 직접 작성한 JSON이므로 문제없지만, 만약 이 파일이 서버에서 동적으로 제공되거나 향후 사용자 입력을 받는 구조로 확장된다면 XSS 취약점이 된다.

이미 `showCards()` 내부의 `front.innerHTML` 패턴도 동일 문제를 이전부터 가지고 있었으나, 이번 커밋에서 `showCardSpotlight()`에도 동일한 패턴을 추가했다. 최소한 `showCardSpotlight()`에서는 DOM API를 직접 사용하거나 간단한 이스케이프 함수를 사용하는 것이 좋다.

---

## Important Improvements (수정 권장)

### 4. CSS 중복 — spotlight-card와 card-front 스타일의 대규모 중복

**위치**: CSS 섹션 126~133번 라인(card-front용) vs 143~155번 라인(spotlight-card용)

두 클래스의 하위 요소들에 적용되는 스타일이 사실상 동일하다:

```css
/* card-front의 하위 요소 */
.card-numeral { font-family:'Cinzel',serif; font-size:.65rem; color:var(--gold-lo); letter-spacing:.2em }
.card-name-en { font-family:'Cinzel',serif; font-size:.7rem; color:var(--gold); ... }
/* spotlight-card의 하위 요소 — 거의 동일 */
.spotlight-card .card-numeral { font-family:'Cinzel',serif; font-size:.65rem; color:var(--gold-lo); letter-spacing:.2em }
.spotlight-card .card-name-en { font-family:'Cinzel',serif; font-size:.7rem; color:var(--gold); ... }
```

`.spotlight-card` 내부에서 이미 존재하는 전역 클래스(`.card-numeral`, `.card-name-en` 등)를 그대로 사용하고 있으므로, `spotlight-card` 전용 override 선언 대부분은 불필요하다. 실제로 `showCardSpotlight()`에서 생성하는 HTML이 `showCards()`에서 생성하는 HTML과 동일한 클래스명을 사용하고 있기 때문에, spotlight-card 전용 스타일 블록의 약 80%는 이미 전역 선언으로 커버된다.

삭제 가능한 라인: 144~151번 라인의 `.spotlight-card .card-numeral` ~ `.spotlight-card .card-keywords` 블록 전체.

**영향**: CSS 약 8줄 감소, 향후 카드 스타일 변경 시 두 곳을 동시에 수정해야 하는 유지보수 부담 제거.

---

### 5. topicKey() 함수 — today/general 토픽 매핑 오류

**위치**: `topicKey()` 함수 1081~1083번 라인

```javascript
function topicKey() {
  var m={love:'love',career:'career',finance:'finance',today:'love',general:'career'};
  return m[selectedTopic]||'love';
}
```

`today`는 `love`로, `general`은 `career`로 매핑된다. 카드 JSON의 `situation_snippets`는 `love`, `career`, `finance` 세 가지 키만 가지고 있으므로 매핑 자체는 기능적으로 동작하지만, 의미론적으로 오류다.

"오늘의 운세"를 선택했을 때 연애 스니펫이 사용되고, "종합운"을 선택했을 때 직업 스니펫이 사용된다. 사용자가 선택한 토픽과 출력되는 내용이 불일치한다. 이 문제는 이번 커밋 이전부터 존재했지만, 이번 커밋에서 `generateAllReadings()`를 더 전면에 배치하면서 더 중요해졌다.

**권장 수정**: `major_arcana.json`에 `today`와 `general` 스니펫을 추가하거나, 최소한 `today`는 `love`/`career`/`finance` 스니펫을 랜덤하게 조합하는 방식으로 처리.

---

### 6. flippedCount 기반 다음 카드 잠금 해제 — off-by-one 위험

**위치**: `playReactionAndRead()` 내 dlgCallback 내부 1216~1217번 라인

```javascript
$cardArea.classList.add('show');
var slots = $cardArea.querySelectorAll('.card-slot');
if (slots[flippedCount]) slots[flippedCount].classList.remove('card-locked');
```

`flippedCount`는 카드가 뒤집힐 때 증가하고(`slot.onclick` 1152번 라인), 이 콜백이 실행될 때는 이미 `flippedCount`가 1 증가한 상태다. 따라서 `slots[flippedCount]`는 "다음 번째" 슬롯을 가리키는 것이 맞다.

그러나 3장 스프레드에서:
- 첫 번째 카드 클릭 후 flippedCount = 1, slots[1]이 잠금 해제 → 두 번째 카드 활성화 (정상)
- 두 번째 카드 클릭 후 flippedCount = 2, slots[2]이 잠금 해제 → 세 번째 카드 활성화 (정상)
- 세 번째 카드 클릭 후 flippedCount = 3, slots[3]은 존재하지 않으므로 if 조건으로 안전하게 처리 (정상)

현재 로직은 수학적으로 정확하다. 다만 `querySelectorAll` 결과가 DOM 순서에 의존하므로, `showCards()`에서 카드가 삽입되는 순서가 변경되면 버그가 된다. 이 부분에 간단한 주석으로 의존관계를 명시하는 것이 좋다.

---

### 7. 스포트라이트 카드 표시 중 cardArea 숨김 — 레이아웃 z-index 경쟁

**위치**: CSS 108번 라인과 138~139번 라인

```css
#cardArea { z-index:55; ... }
#cardSpotlight { z-index:58; ... }
```

카드 스포트라이트가 표시될 때 `$cardArea.classList.remove('show')`로 카드 영역을 숨긴다. 이는 opacity:0에 pointer-events:none이 되는 것이다. z-index 58의 스포트라이트와 z-index 55의 cardArea가 시각적으로 겹치는 과도기(transition 0.6s 동안)에 두 요소가 동시에 화면에 존재한다.

`$cardArea`에 transition이 `all .6s`로 설정되어 있어 사라지는 데 600ms가 걸리고, `#cardSpotlight`도 동일하게 600ms 트랜지션으로 나타난다. 두 애니메이션이 동시에 시작되면 약 300ms 시점에서 두 요소가 모두 반투명한 상태로 겹쳐 보이는 현상이 발생할 수 있다. 스포트라이트가 cardArea 위에(z-index 높음) 있으므로 시각적으로 크게 문제는 아니지만, 저사양 기기에서 렌더링이 지저분하게 보일 수 있다.

**권장**: cardArea를 숨긴 뒤 200~300ms 딜레이 후 스포트라이트를 표시하거나, `$cardArea` 숨기기를 즉각적으로 처리.

---

### 8. replayCard() 함수 — 스포트라이트가 이미 표시된 경우 처리 없음

**위치**: `replayCard()` 함수 1320~1332번 라인

```javascript
function replayCard(idx) {
  var card = drawnCards[idx];
  var cardReading = storedReadings.cards[idx];
  var ch = selectedChar;
  showCardSpotlight(card);    // 스포트라이트 표시
  cardReading.lines.forEach(function(line) {
    say(ch, line);
  });
  dlgCallback = function() {
    hideCardSpotlight();
    showReplayOptions();
  };
}
```

`showReplayOptions()` 직후 `replayCard()`가 여러 번 연속 호출되면 (사용자가 두 카드를 연속으로 "다시 듣기" 선택 시), 이전 스포트라이트가 숨겨지지 않은 상태에서 새 스포트라이트가 `innerHTML`을 덮어쓰게 된다. 이는 기능적으로는 문제없이 동작하지만, 트랜지션 없이 내용이 바뀌어 시각적으로 어색하다.

`showCardSpotlight()` 앞에 `hideCardSpotlight()`를 먼저 호출하거나, 이미 표시 중인 경우 먼저 숨기고 transition 완료 후 새 카드를 표시하는 것이 더 매끄럽다.

---

## Minor Suggestions (선택적 개선)

### 9. 하드코딩된 문자열 대화 중복

**위치**: `playReactionAndRead()` 내 1220~1232번 라인

"다음 카드를 뒤집어 주세요" 류의 프롬프트가 `playReactionAndRead()` 함수 내부에 인라인으로 정의되어 있다. 이전 구현의 `deliverCardReading(cardIdx)` 함수에서는 "다음 카드 전환" 대사가 별도 블록으로 분리되어 있었다. 현재 구현에서는 "다음 카드를 눌러달라"는 프롬프트가 `playReactionAndRead()` 함수 내에 섞여 있어 단일 책임 원칙에서 벗어난다.

상수 배열로 추출하는 것을 권장한다:

```javascript
var PROMPT_NEXT_CARD_BT = [
  '...자, 다음 카드를 뒤집어 주세요.',
  '...이어서, 다음 카드를 선택해 주세요.',
  '...그럼, 다음 카드를 눌러주세요.'
];
var PROMPT_NEXT_CARD_RB = [
  '자~ 다음 카드를 뒤집어봐!',
  '다음 카드를 눌러봐~ 깡총!',
  '다음 카드가 기다리고 있어!'
];
```

---

### 10. reversed 애니메이션 키프레임 충돌

**위치**: CSS 143번, 152번 라인

```css
.spotlight-card.reversed { transform: rotate(180deg) }
@keyframes spotlightAppear { from { ... transform:scale(.7) translateY(30px) } to { opacity:1; transform:scale(1) translateY(0) } }
```

`.spotlight-card.reversed`에 `transform: rotate(180deg)`가 설정되어 있는데, `spotlightAppear` 키프레임이 `transform`을 직접 조작한다. CSS에서 `transform` 속성은 값 전체를 교체하므로, 애니메이션이 실행되는 동안 `rotate(180deg)`가 keyframe의 `scale()`과 `translateY()`로 덮어써진다.

결과적으로 역위치 카드의 등장 애니메이션 중에는 회전이 적용되지 않고, 애니메이션이 끝난 후에야 `rotate(180deg)`가 적용된다. 이는 시각적으로 부자연스럽다.

**권장 수정**: keyframe을 `transform: scale(.7) translateY(30px) rotate(180deg)` / `transform: scale(1) translateY(0) rotate(180deg)` 형태로 조건부 처리하거나, `reversed` 클래스 대신 JavaScript에서 직접 `style.transform`을 설정하도록 변경.

---

### 11. CARD_SYMBOLS 배열 — 인덱스 0 (The Fool) 심볼 불일치

**위치**: 416~419번 라인

```javascript
var CARD_SYMBOLS = [
  '🃏','⚡','🌙','🌟','👑','🏛','❤️','⚔️','⚖️','🔦',
  '🎡','💪','🔄','💀','🌿','😈','🏰','⭐','🌕','☀️','🔔','🌍'
];
```

카드 번호는 0(바보)부터 21(세계)까지이며, CARD_SYMBOLS도 22개로 맞게 정의되어 있다. 그러나 `showCardSpotlight()`와 `showCards()` 양쪽에서 동일하게 `CARD_SYMBOLS[card.number]||'✦'`를 사용한다. 이 배열에 대한 참조가 두 함수에 분산되어 있으므로, 배열 접근 로직을 별도 헬퍼 함수 `getCardSymbol(number)`로 추출하면 유지보수가 쉬워진다.

---

### 12. toRoman() 함수 — 0번 카드 처리

**위치**: 1246~1251번 라인

```javascript
function toRoman(n){
  if(!n) return '0';
  ...
}
```

The Fool 카드(number: 0)가 들어오면 `!n`이 `true`가 되어 `'0'`을 반환한다. 실제로 The Fool은 로마 숫자가 없거나 `0`으로 표기하는 것이 관례이므로, 기능적으로는 맞다. 그러나 `'0'`이 아닌 빈 문자열이나 `'0'` 대신 타로 전통에 따른 아라비아 숫자 `'0'`을 명시적으로 처리하는 주석이 있으면 의도가 명확해진다.

---

## Architecture Considerations

### 단일 HTML 파일 구조의 한계

이 프로젝트는 1849줄의 단일 HTML 파일에 CSS, JavaScript, HTML이 모두 포함되어 있다. 현재 규모에서는 관리 가능하지만, 기능이 추가될수록 다음 문제가 심화될 것이다:

1. **대화/반응 데이터와 로직의 혼재**: `RX_BT_UP`, `RX_BT_REV` 등의 반응 패턴 배열, `generateAllReadings()` 내부의 긴 텍스트 배열들이 로직 코드와 섞여 있다. 이 텍스트들을 `data/` 폴더의 JSON 파일로 분리하면 카드 데이터와 동일한 방식으로 관리할 수 있다.

2. **전역 상태 관리**: `selectedChar`, `flippedCount`, `drawnCards`, `storedReadings`, `dlgCallback` 등 10여 개의 전역 변수가 함수들 사이에서 암묵적으로 공유된다. `dlgCallback`의 중첩 재할당 버그(Critical Issue #1)도 이 구조에서 기인한다. 최소한 상태를 `gameState` 단일 객체로 묶어 관리하는 것을 권장한다.

3. **dlgCallback 아키텍처의 본질적 취약성**: 이전 구현의 `deliverCardReading(cardIdx)` 재귀 패턴이 훨씬 안전하다. 재귀 패턴은 현재 처리 중인 카드 인덱스를 함수 스택에 캡슐화하므로 외부에서 상태를 건드릴 수 없다. 새 구현의 중첩 클로저 패턴은 한 단계 더 복잡해지면서 안전성이 낮아졌다.

### 성능 고려

- `showCardSpotlight()`가 호출될 때마다 `innerHTML`로 DOM을 전체 재생성한다. 스포트라이트의 내용이 바뀌는 것은 피할 수 없지만, 불필요한 DOM 재생성을 줄이기 위해 첫 생성 후 내부 텍스트 노드만 갱신하는 방식이 더 효율적이다.
- CSS 애니메이션 `spotlightGlow`가 `box-shadow`를 변경하는데, `box-shadow` 변경은 repaint를 유발한다. `filter: drop-shadow()` 또는 `opacity` 기반 레이어 분리로 대체하면 GPU 가속을 활용할 수 있다.

---

## Next Steps

우선순위 순서로 권장 작업:

1. **[Critical]** `playReactionAndRead()` 함수 내에 `isReadingInProgress` 플래그 또는 카드 잠금 강화 로직 추가 — dlgCallback 경쟁 조건 방지
2. **[Critical]** `storedReadings.cards[idx]` 접근 시 null 체크 방어 코드 추가
3. **[Important]** CSS spotlight-card 하위 요소 스타일 중복 제거 (8줄 감소)
4. **[Important]** `.spotlight-card.reversed`와 `spotlightAppear` 키프레임 transform 충돌 수정
5. **[Minor]** `topicKey()` 매핑 개선 — today/general 토픽 스니펫 분리
6. **[Minor]** 대화 프롬프트 배열을 상수로 추출
