"""Text quality utilities -- garbled text detection.

Extracted from page_classifier.py to enable reuse by both
page_classifier and block_quality_verifier. Single source of truth
for garbled text detection -- duplicate implementations are forbidden.
"""

from __future__ import annotations

import re


def _readable_stats(text: str) -> tuple[int, int]:
    """Count readable and total non-whitespace characters.

    Returns:
        (readable_count, total_count)
    """
    readable_count = 0
    total_count = 0
    for ch in text:
        if ch.isspace():
            continue
        total_count += 1
        if (
            '가' <= ch <= '힣'  # Korean syllables
            or 'ㄱ' <= ch <= 'ㆎ'  # Korean jamo
            or 'A' <= ch <= 'Z'
            or 'a' <= ch <= 'z'
            or '0' <= ch <= '9'
            or ch in '.,;:!?()-/\\[]{}@#$%&*+=<>~`\'"'
            or '①' <= ch <= '⑳'  # circled numbers
            or ch in '·…―'
        ):
            readable_count += 1
    return readable_count, total_count


# Korean jamo ranges
_CONSONANTS = set(range(0x3131, 0x314F))  # ㄱ-ㅎ
_VOWELS = set(range(0x314F, 0x3164))  # ㅏ-ㅣ

# Pattern: digit immediately adjacent to Korean syllable (no space)
_DIGIT_HANGUL_MIX_RE = re.compile(r'[0-9][가-힣]|[가-힣][0-9]')

# Pattern: digit separated from Korean syllable by whitespace ("0 서")
_DIGIT_SPACE_HANGUL_RE = re.compile(r'[0-9]\s+[가-힣]|[가-힣]\s+[0-9]')

# Common Korean bigrams that appear in natural text
# These are 2-syllable combinations that are frequent in Korean
_COMMON_PARTICLES = frozenset({
    "의", "가", "이", "을", "를", "은", "는", "에", "도", "로",
    "와", "과", "한", "인", "된", "된", "하", "고", "서", "며",
    "다", "라", "나", "지", "수", "것", "때", "대", "기", "적",
})

# Common Korean words that start with ㅇ (ieung) initial consonant.
# These are natural words where all syllables happen to start with ㅇ,
# so they should NOT be flagged as garbled nonsense.
_COMMON_KOREAN_WORDS = frozenset({
    "이야기", "우리", "어디", "아이", "오이", "이어",
    "아우", "여우", "오아", "우아", "이유", "의의",
    "이익", "의욕", "이용", "위원", "의원", "운영",
    "요약", "우위", "유의", "유아", "유용", "유익",
    "이외", "이웃", "이전", "아직", "이후", "여행",
    "유지", "역할", "원인", "영향", "연구", "예정",
    "용도", "의무", "인원", "인식", "인정", "우선",
    "의견", "일반", "이상", "이하", "이내", "이름",
    "운동", "위치", "위험", "원칙", "월요일", "요일",
    "응답", "약속", "역사", "연락", "연습", "열심",
    "영어", "오전", "오후", "완전", "요청", "용어",
    "우수", "원래", "위반", "유사", "육아", "읽기",
    "입장", "있어", "있음", "없어", "없음",
})


def _korean_garbled_score(text: str) -> float:
    """Score how garbled a Korean text looks (0.0 = normal, 1.0 = garbled).

    Six heuristics are computed independently.  The final score uses a
    ``max(weighted_sum, strongest_signal)`` strategy so that any single
    strong indicator can push the result above the 0.3 detection
    threshold.

    Heuristics
    ----------
    1. Isolated jamo ratio -- standalone consonants/vowels vs syllables
    2. Abnormal bigram score -- jamo-syllable pairs and jamo sequences
    3. Digit-Korean mixing -- digits glued to syllables unnaturally
    4. Word fragmentation -- many 1-syllable "words" from broken OCR
    5. Nonsense word score -- digit+syllable tokens, all-ieung words
    6. Digit-word ratio -- sequences of isolated single-digit tokens
    """
    stripped = text.strip()
    if not stripped:
        return 0.0

    # ------------------------------------------------------------------
    # Character-type census
    # ------------------------------------------------------------------
    syllable_count = 0  # Complete Korean syllables (가-힣)
    jamo_count = 0      # Isolated jamo (ㄱ-ㅎ, ㅏ-ㅣ)
    digit_count = 0
    total_meaningful = 0

    for ch in stripped:
        if ch.isspace():
            continue
        cp = ord(ch)
        total_meaningful += 1
        if 0xAC00 <= cp <= 0xD7A3:  # 가-힣
            syllable_count += 1
        elif cp in _CONSONANTS or cp in _VOWELS:
            jamo_count += 1
        elif '0' <= ch <= '9':
            digit_count += 1

    korean_count = syllable_count + jamo_count
    if korean_count == 0:
        return 0.0

    # ------------------------------------------------------------------
    # 1. Isolated jamo ratio
    # ------------------------------------------------------------------
    jamo_ratio = jamo_count / korean_count if korean_count > 0 else 0.0

    # ------------------------------------------------------------------
    # 2. Abnormal bigram score
    # ------------------------------------------------------------------
    chars = [ch for ch in stripped if not ch.isspace()]
    abnormal_pairs = 0

    for i in range(len(chars) - 1):
        c1, c2 = chars[i], chars[i + 1]
        cp1, cp2 = ord(c1), ord(c2)
        c1_is_jamo = cp1 in _CONSONANTS or cp1 in _VOWELS
        c2_is_jamo = cp2 in _CONSONANTS or cp2 in _VOWELS

        if c1_is_jamo and c2_is_jamo:
            abnormal_pairs += 1
        elif c1_is_jamo or c2_is_jamo:
            c1_is_syllable = 0xAC00 <= cp1 <= 0xD7A3
            c2_is_syllable = 0xAC00 <= cp2 <= 0xD7A3
            if (c1_is_jamo and c2_is_syllable) or (c1_is_syllable and c2_is_jamo):
                abnormal_pairs += 1

    bigram_score = 0.0
    if korean_count > 1:
        bigram_score = min(1.0, abnormal_pairs / max(korean_count - 1, 1))

    # ------------------------------------------------------------------
    # 3. Digit-Korean mixing ratio
    # ------------------------------------------------------------------
    mix_matches = _DIGIT_HANGUL_MIX_RE.findall(stripped)
    space_mix_matches = _DIGIT_SPACE_HANGUL_RE.findall(stripped)

    mix_ratio = 0.0
    if total_meaningful > 0:
        natural_digit_patterns = len(re.findall(
            r'(?:제|약|총|전|현|매|각|이)\d|'
            r'\d+(?:년|월|일|장|조|항|호|절|편|관|개|명|건|원|세|%|회|차|번|권|급|종)',
            stripped,
        ))
        total_mix = len(mix_matches) + len(space_mix_matches)
        unnatural_mixes = max(0, total_mix - natural_digit_patterns)
        if unnatural_mixes > 0 and syllable_count > 0:
            mix_ratio = min(1.0, unnatural_mixes / max(syllable_count, 1))

    # ------------------------------------------------------------------
    # 4. Word fragmentation score
    # ------------------------------------------------------------------
    words = stripped.split()
    korean_words: list[tuple[int, int, str]] = []
    for w in words:
        ksyl = sum(1 for c in w if 0xAC00 <= ord(c) <= 0xD7A3)
        kjam = sum(1 for c in w if ord(c) in _CONSONANTS or ord(c) in _VOWELS)
        if ksyl + kjam > 0:
            korean_words.append((ksyl, kjam, w))

    frag_score = 0.0
    if len(korean_words) >= 3:
        single_syl_count = 0
        for ksyl, kjam, w in korean_words:
            total_k = ksyl + kjam
            if total_k == 1 and ksyl == 1 and w not in _COMMON_PARTICLES:
                if len(w) == 1 or (len(w) == 2 and w[0].isdigit()):
                    single_syl_count += 1
        frag_score = min(1.0, single_syl_count / max(len(korean_words), 1))

        _VOWEL_INITIAL = frozenset("으어이오우아에의")
        vowel_fragment_count = sum(
            1 for ksyl, kjam, w in korean_words
            if ksyl == 1 and kjam == 0 and len(w) == 1 and w in _VOWEL_INITIAL
        )
        if len(korean_words) > 0:
            vowel_frag_ratio = vowel_fragment_count / len(korean_words)
            frag_score = max(frag_score, vowel_frag_ratio)

    # ------------------------------------------------------------------
    # 5. Nonsense word score
    # ------------------------------------------------------------------
    nonsense_score = 0.0
    if len(korean_words) >= 2:
        nonsense_count: float = 0
        for ksyl, kjam, w in korean_words:
            # Digit + Korean compound token (e.g. "0서", "0노포가")
            # BUT skip natural patterns like "2024년", "3월", "10건"
            has_digit = any(c.isdigit() for c in w)
            has_syl = any(0xAC00 <= ord(c) <= 0xD7A3 for c in w)
            if has_digit and has_syl and len(w) >= 2:
                is_natural = bool(re.match(
                    r'^(?:'
                    # "제1장", "각2항" etc. -- Korean prefix + digit + counter
                    r'(?:제|약|총|전|현|매|각|이)\d+(?:년|월|일|장|조|항|호|절|편|관|개|명|건|원|세|회|차|번|권|급|종)'
                    r'|'
                    # "2024년", "10,000원" etc. -- digits (with commas) + counter
                    r'\d[\d,]*(?:년|월|일|장|조|항|호|절|편|관|개|명|건|원|세|회|차|번|권|급|종)'
                    r')$',
                    w,
                ))
                if not is_natural:
                    nonsense_count += 1
                    continue
            # Word entirely of vowel-initial syllables (all start with ㅇ)
            if ksyl >= 2 and kjam == 0:
                vowel_initial_syls = sum(
                    1 for c in w if 0xAC00 <= ord(c) <= 0xD7A3
                    and (ord(c) - 0xAC00) // 588 == 11  # ㅇ index
                )
                if vowel_initial_syls == ksyl:
                    if w not in _COMMON_KOREAN_WORDS:
                        nonsense_count += 1
        nonsense_score = min(1.0, nonsense_count / max(len(korean_words), 1))

    # ------------------------------------------------------------------
    # 6. Digit-word ratio (isolated digit tokens like "0 0 0")
    # ------------------------------------------------------------------
    digit_word_ratio = 0.0
    if len(words) >= 3:
        digit_words = sum(1 for w in words if w.isdigit() and len(w) == 1)
        if digit_words >= 2:
            digit_word_ratio = min(1.0, digit_words / len(words))

    # ------------------------------------------------------------------
    # Combine: weighted sum PLUS max-signal boost
    # ------------------------------------------------------------------
    weighted = (
        jamo_ratio * 0.25
        + bigram_score * 0.20
        + mix_ratio * 0.20
        + frag_score * 0.10
        + nonsense_score * 0.15
        + digit_word_ratio * 0.10
    )

    # If any single strong signal is present, use the strongest one
    # scaled so it alone can cross the 0.3 threshold.
    max_signal = max(
        jamo_ratio * 0.70,
        bigram_score * 0.55,
        mix_ratio * 0.45,
        nonsense_score * 0.75,
        digit_word_ratio * 0.55,
    )

    return min(1.0, max(weighted, max_signal))


def _has_korean(text: str) -> bool:
    """Check if text contains any Korean characters."""
    for ch in text:
        cp = ord(ch)
        if (0xAC00 <= cp <= 0xD7A3  # 가-힣
                or cp in _CONSONANTS
                or cp in _VOWELS):
            return True
    return False


def is_garbled_text(raw_text: str) -> bool:
    """Detect text that was extracted but is unreadable (custom font encoding).

    Returns True when the readable character ratio falls below 0.3
    (PUA-based), or when Korean garbled score >= 0.3.
    Empty or whitespace-only strings return False.

    This is the canonical implementation -- page_classifier.py and
    block_quality_verifier.py both import from here.
    """
    stripped = raw_text.strip()
    if not stripped:
        return False
    readable_count, total_count = _readable_stats(stripped)
    if total_count == 0:
        return False
    # Original PUA-based threshold: garbled when < 30% readable
    if readable_count / total_count < 0.3:
        return True
    # Korean-specific: check pattern-based garbled score
    if _has_korean(stripped):
        return _korean_garbled_score(stripped) >= 0.3
    return False


def garbled_ratio(raw_text: str) -> float:
    """Return the ratio of garbled (non-readable) characters (0.0 -- 1.0).

    0.0 = fully normal, 1.0 = fully garbled.
    Empty or whitespace-only strings return 0.0.

    Combines PUA-based detection with Korean-specific pattern analysis.
    """
    stripped = raw_text.strip()
    if not stripped:
        return 0.0
    readable_count, total_count = _readable_stats(stripped)
    if total_count == 0:
        return 0.0

    pua_garbled = 1.0 - (readable_count / total_count)

    # Korean-specific garbled detection
    if _has_korean(stripped):
        korean_score = _korean_garbled_score(stripped)
        return max(pua_garbled, korean_score)

    return pua_garbled


__all__ = [
    "is_garbled_text",
    "garbled_ratio",
]
