"""Capture Parser v1: CJK amount normalization.

Pre-extraction stage that rewrites Simplified Chinese numeral tokens
(``五百五``, ``一千二``, ``五百块``, …) into the existing :class:`Token`
shape with ``is_numeric_like=True`` so the downstream amount extractor
sees them as ordinary numeric candidates.

Contract (mirrors :mod:`app.services.parser.tokens` /
:mod:`app.services.parser.amount`):

1. Pure function — no DB, no I/O, no side effects.
2. Operates on the token list returned by :func:`tokenize`.
3. Returns a new list of :class:`Token` instances; the input list is
   not mutated. Token instances are frozen.
4. Rewritten tokens preserve the original chunk's ``span`` so source-
   span diagnostics still point at the user's typed text.
5. Tokens with a recognised money suffix (``块``/``元``/``澳币``/``澳元``)
   are emitted as a synthetic ``$`` currency token (zero-width span at
   the chunk start) followed by the rewritten numeric token. This lets
   the existing amount scorer treat them as AUD-tier without any new
   branch in :mod:`amount`.

Supported forms (Simplified Chinese only):

* Digits: ``零 一 二 三 四 五 六 七 八 九`` plus ``〇`` (= 0) and
  ``两`` (= 二)
* Sub-万 places: ``十 百 千``
* 万 place marker: ``万``
* Money suffixes: ``块 元 澳币 澳元``
* Integer values in ``[1, 99_999_999]`` (well below the $10M downstream
  cap in ``expenses._validate_save``)

Safety rules:

* Hard char whitelist — any character outside the small allowed set
  rejects the entire token (so date words like ``五月`` and site words
  like ``工地一`` fail at gate 1).
* Bare single CJK digit (``五`` / ``九`` / ``一`` alone) is rejected
  unless a money suffix is attached (``五块`` IS accepted as $5).
* Traditional / financial numerals (``壹貳參…``) and sub-yuan units
  (``角``/``分``) are NOT supported.
* 亿-level numerals (``亿`` and double-``万``) are NOT supported.

Confidence handling (downstream of this stage):

* Bare CJK numeral → bare-integer tier (0.5 in the extractor).
* CJK numeral with money suffix → AUD-integer tier (0.9), via the
  synthetic ``$`` predecessor.
* CJK numerals never produce a decimal-tier candidate (no ``角``/``分``).

Mixed Arabic + CJK input goes through the existing tie-handling: same-
tier candidates collapse to ``ambiguous=True`` and route the expense to
review via the existing ``amount_uncertain`` reason.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.parser.tokens import Token

# ---------------------------------------------------------------------------
# Character tables
# ---------------------------------------------------------------------------

# Digit characters → integer value. ``零``/``〇`` map to 0 (used for the
# zero-skip rule). ``两`` is treated as a synonym for ``二`` (informal
# but very common in spoken Chinese, especially before 千 and 万).
_CJK_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

# Zero-skip markers. ``零``/``〇`` mean "the following digit lands in a
# lower (ultimately the ones) place" — e.g. ``一万零五`` = 10005, not
# 15000. Their presence in a 万-split remainder disables the colloquial
# trailing-digit shift below.
_CJK_ZERO_CHARS: frozenset[str] = frozenset({"零", "〇"})

# Sub-``万`` place markers: handled inside :func:`_parse_sub_wan`.
_CJK_SUB_WAN_PLACES: dict[str, int] = {
    "十": 10,
    "百": 100,
    "千": 1000,
}

# ``万`` is handled at the split level in :func:`_parse_cjk_numeral`,
# NOT inside :func:`_parse_sub_wan`. Keeping it separate prevents
# malformed inputs like ``一万千`` from accidentally producing values.
_WAN = "万"

# Hard whitelist: every char in a CJK numeral string (after stripping
# any money suffix) MUST be in this set. Anything else disqualifies the
# token at gate 1 — this is the primary defence against false positives
# on date words (``五月``, ``五号``, ``五日``) and site words
# (``工地一``).
_ALL_NUMERAL_CHARS: frozenset[str] = (
    frozenset(_CJK_DIGITS.keys())
    | frozenset(_CJK_SUB_WAN_PLACES.keys())
    | frozenset({_WAN})
)

# Money suffixes — try LONGEST first so ``澳币`` / ``澳元`` strip
# cleanly even though they share a leading character with single-char
# suffixes. ``块`` is the colloquial spoken word for "dollar"; ``元`` is
# formal; ``澳币`` / ``澳元`` are explicitly Australian.
_MONEY_SUFFIXES: tuple[str, ...] = ("澳币", "澳元", "块", "元")

# Hard upper bound on parsed CJK values. Sits below the downstream
# ``_MAX_AMOUNT_INC_GST = Decimal("10000000")`` cap in
# :mod:`app.services.expenses` so even a pathological CJK input cannot
# bypass the existing CHP-4 amount-cap path.
_MAX_CJK_VALUE: int = 99_999_999


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_money_suffix(text: str) -> tuple[str, bool]:
    """Strip a recognised money suffix from the end of ``text``.

    Returns ``(stripped_text, had_suffix)``. Tries the longest suffix
    first. If ``text`` IS a bare suffix with nothing else (e.g. just
    ``"块"``), the suffix is NOT stripped (no numeric prefix to work
    with) and the function returns ``(text, False)``.
    """
    for suffix in _MONEY_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)], True
    return text, False


def _parse_sub_wan(text: str) -> int | None:
    """Parse a CJK numeral string in the range ``[0, 9999]``.

    Handles place markers ``十`` / ``百`` / ``千`` with the colloquial
    trailing-shift rule (``五百五`` → 550, NOT 505) and the zero-skip
    rule (``一百零五`` → 105, NOT 150).

    Returns ``None`` on syntax error (e.g. two consecutive digits, or a
    character outside the sub-``万`` set). Empty string returns ``0``.

    NOTE: this helper does NOT enforce gate 2 (the "must contain a
    place marker" rule). The caller in :func:`_parse_cjk_numeral` does.
    A standalone bare digit fed here would happily return its value —
    that's intentional because the sub-``万`` parser is also called on
    the high/low parts of a 万-split, and those parts can legitimately
    be a bare single digit (``三万二`` → high=``三``=3, low=``二``=2 with
    shift).
    """
    if not text:
        return 0

    accumulator = 0
    pending_digit: int | None = None
    last_place: int | None = None
    saw_zero_skip = False

    for ch in text:
        if ch in _CJK_DIGITS:
            digit = _CJK_DIGITS[ch]
            if digit == 0:
                # ``零`` / ``〇`` — acts as a place-skip marker. Reset
                # any pending digit and remember we saw a skip; the
                # next trailing digit (if any) will land in ones place.
                saw_zero_skip = True
                pending_digit = None
            else:
                if pending_digit is not None:
                    # Two consecutive digits without a place marker is
                    # not valid grammar (e.g. ``五五`` is meaningless).
                    return None
                pending_digit = digit
        elif ch in _CJK_SUB_WAN_PLACES:
            place = _CJK_SUB_WAN_PLACES[ch]
            if last_place is not None and place >= last_place:
                # A place marker must be strictly smaller than the previous
                # one. A repeated or ascending place (``十十``, ``千千``,
                # ``十百``) is malformed grammar — reject rather than
                # silently accumulate a plausible-looking value that would
                # then skip the review queue at high confidence (audit C-1).
                return None
            if pending_digit is None:
                # Bare ``十`` / ``百`` / ``千`` — implicit leading 1.
                # ``十二`` → 1*10 + 2 = 12; ``十`` → 10; ``百`` → 100.
                pending_digit = 1
            accumulator += pending_digit * place
            last_place = place
            pending_digit = None
            saw_zero_skip = False
        else:
            # Includes ``万`` and any non-numeral char — should be
            # filtered by the caller's gate, but fail safely.
            return None

    # End of string — handle any trailing digit.
    if pending_digit is not None:
        if last_place is None:
            # No place markers at all. Bare digit. Filtered by gate 2
            # for the no-suffix path; reachable only via the 万-split
            # high/low parts. Defensive: add to ones.
            accumulator += pending_digit
        elif saw_zero_skip:
            # Trailing digit after ``零``-skip lands in ones place.
            # ``一百零五`` → 100 + 5 = 105.
            accumulator += pending_digit
        else:
            # Colloquial shift: trailing digit goes one place below
            # the last seen place marker. ``五百五`` → 500 + 5*10 = 550.
            shift_place = last_place // 10
            if shift_place < 1:
                shift_place = 1  # Defensive; unreachable via valid grammar.
            accumulator += pending_digit * shift_place

    return accumulator


# Mixed Arabic-digit + CJK forms (audit C-2): the common bilingual way an
# operator types an amount — an Arabic number with a Chinese magnitude/suffix.
# Only fires as a FALLBACK when the pure-CJK parser returns None, and (on the
# no-suffix path) only when a CJK magnitude marker is present, so pure-Arabic
# tokens keep their existing (numeric-like) handling untouched.
_MIXED_MAGNITUDES: dict[str, int] = {"万": 10000, "千": 1000, "百": 100, "十": 10}
_MIXED_ARABIC_MAG_RE = re.compile(r"^(\d+)([万千百十])$")
_MIXED_ARABIC_WAN_TAIL_RE = re.compile(r"^(\d+)万(\d+)([千百十])?$")


def _parse_mixed_amount(text: str) -> Decimal | None:
    """Parse a mixed Arabic+CJK amount (``100``, ``5千``, ``3万5``, ``10万5千``).

    Returns ``None`` for anything not in the small, unambiguous set below —
    ambiguous multi-digit tails (``3万50``) fall through to ``None`` so they
    stay routed to review rather than guessed.
    """
    if text.isdigit():
        value = int(text)
        return Decimal(value) if 1 <= value <= _MAX_CJK_VALUE else None

    m = _MIXED_ARABIC_MAG_RE.match(text)
    if m:
        value = int(m.group(1)) * _MIXED_MAGNITUDES[m.group(2)]
        return Decimal(value) if 1 <= value <= _MAX_CJK_VALUE else None

    m = _MIXED_ARABIC_WAN_TAIL_RE.match(text)
    if m:
        high = int(m.group(1))
        low_raw = m.group(2)
        low = int(low_raw)
        tail_mag = m.group(3)
        if tail_mag is not None:
            low_value = low * _MIXED_MAGNITUDES[tail_mag]
        elif low_raw.startswith("0"):
            # Leading-zero tail is a ``零``-skip: the digit lands at face
            # value in the ones place. ``3万05`` → 30005, not 35000.
            low_value = low
        elif low <= 9:
            # A bare single trailing digit shifts one place below 万 (千).
            # ``3万5`` → 30000 + 5*1000 = 35000.
            low_value = low * 1000
        else:
            # Ambiguous multi-digit tail with no magnitude — do not guess.
            return None
        value = high * 10000 + low_value
        return Decimal(value) if 1 <= value <= _MAX_CJK_VALUE else None

    return None


def _has_cjk_magnitude(text: str) -> bool:
    return any(ch in _CJK_SUB_WAN_PLACES or ch == _WAN for ch in text)


def _parse_cjk_numeral(text: str) -> Decimal | None:
    """Parse a pure CJK numeral string (no money suffix) into a Decimal.

    Returns ``None`` if the string fails any of the safety gates:

    * Empty.
    * Any character outside ``_ALL_NUMERAL_CHARS`` (gate 1).
    * No place marker present (gate 2 — rejects bare single digits like
      ``五`` and ``九``).
    * Contains more than one ``万`` (亿-level out of scope).
    * Result outside ``[1, _MAX_CJK_VALUE]``.
    """
    if not text:
        return None

    # Gate 1: char whitelist.
    if any(ch not in _ALL_NUMERAL_CHARS for ch in text):
        return None

    # Gate 2: must contain at least one place marker (sub-万 or 万).
    has_place = any(
        ch in _CJK_SUB_WAN_PLACES or ch == _WAN for ch in text
    )
    if not has_place:
        return None

    if _WAN in text:
        # Split at the LAST 万 (e.g. ``三万二`` → high=``三``, low=``二``).
        idx = text.rindex(_WAN)
        high_text = text[:idx]
        low_text = text[idx + 1 :]

        # Reject ``万`` appearing on either side after split — that
        # means there were 2+ ``万`` characters, which is 亿-level.
        if _WAN in high_text or _WAN in low_text:
            return None

        # High part: empty means implicit 1 (so ``万`` alone = 10000;
        # rare in real input but consistent grammar).
        if not high_text:
            high_value = 1
        else:
            sub = _parse_sub_wan(high_text)
            if sub is None or sub == 0:
                # ``零万X`` is not valid Chinese.
                return None
            high_value = sub

        # Low part: empty means no carry; otherwise apply the same
        # parser, with the colloquial shift rule when low is a single
        # bare digit (``三万二`` → 30000 + 2*1000 = 32000).
        if not low_text:
            low_value = 0
        elif low_text[0] in _CJK_SUB_WAN_PLACES:
            # A low part that starts with a bare place marker (``一万千``,
            # ``三万千``) is malformed — a valid remainder after ``万``
            # begins with a digit (``三万五千``) or the ``零`` skip
            # (``一万零五百``). Reject rather than silently accept (C-1).
            return None
        else:
            sub = _parse_sub_wan(low_text)
            if sub is None:
                return None
            if (
                sub > 0
                and not any(ch in _CJK_SUB_WAN_PLACES for ch in low_text)
                and not any(ch in _CJK_ZERO_CHARS for ch in low_text)
            ):
                # Trailing single digit shift: one place lower than
                # 万 = 千 (1000). ``三万二`` → 30000 + 2*1000 = 32000.
                # Suppressed when a ``零`` skip is present: ``一万零五``
                # → 10005 (the 五 lands in the ones place), not 15000.
                low_value = sub * 1000
            else:
                low_value = sub

        total = high_value * 10000 + low_value
    else:
        sub = _parse_sub_wan(text)
        if sub is None:
            return None
        total = sub

    if total < 1 or total > _MAX_CJK_VALUE:
        return None

    return Decimal(total)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cjk_to_decimal(text: str) -> tuple[Decimal | None, bool]:
    """Try to parse ``text`` as a CJK money amount.

    Returns ``(value, had_money_suffix)``:

    * ``(Decimal(N), True)`` — CJK numeral with a recognised money
      suffix (``五百块`` → ``(Decimal(500), True)``).
    * ``(Decimal(N), False)`` — bare CJK numeral with at least one
      place marker (``五百五`` → ``(Decimal(550), False)``).
    * ``(None, False)`` — not a recognised CJK amount (date word, site
      word, single bare digit without suffix, non-CJK input, or out of
      range).

    Single bare CJK digit + suffix IS accepted (``五块`` → ``(Decimal(5),
    True)``). Single bare CJK digit alone is NOT accepted (``五`` →
    ``(None, False)``).
    """
    if not text:
        return None, False

    stripped, had_suffix = _strip_money_suffix(text)

    if had_suffix:
        # With suffix, single-digit values are accepted (``五块`` → $5).
        if not stripped:
            return None, False
        if len(stripped) == 1 and stripped in _CJK_DIGITS:
            digit = _CJK_DIGITS[stripped]
            if digit < 1:
                # ``零块`` — zero amount rejected.
                return None, False
            return Decimal(digit), True
        # Multi-char with suffix: same gate logic as the no-suffix
        # path. Multi-char digit-only strings (``五五块``) fail gate 2
        # because no place marker is present.
        value = _parse_cjk_numeral(stripped)
        if value is None:
            # C-2 fallback: mixed Arabic+CJK or Arabic-only with a money
            # suffix (``100元`` → 100, ``3万5块`` → 35000).
            value = _parse_mixed_amount(stripped)
        if value is None:
            return None, False
        return value, True

    # No suffix path — gate 2 enforces "must have place marker".
    value = _parse_cjk_numeral(stripped)
    if value is None and _has_cjk_magnitude(stripped):
        # C-2 fallback for mixed Arabic+CJK magnitudes (``5千`` → 5000,
        # ``3万5`` → 35000). Gated on a CJK magnitude char so pure-Arabic
        # tokens keep their existing numeric-like handling.
        value = _parse_mixed_amount(stripped)
    if value is None:
        return None, False
    return value, False


def normalize_cjk_amount_tokens(tokens: list[Token]) -> list[Token]:
    """Rewrite CJK money-amount tokens in the input list.

    Walks the input. For each token that is NOT already
    ``is_numeric_like`` and NOT a currency symbol, attempts
    :func:`cjk_to_decimal` on its ``text``. On a hit:

    * **With money suffix** — emits a synthetic ``$`` currency token
      (zero-width span at the chunk start) followed by a numeric token
      that preserves the original chunk's full span and has its
      ``normalized`` field set to the Arabic decimal string.
    * **Without suffix** — emits a single numeric token (full original
      span, ``normalized`` = Arabic decimal string).

    All other tokens pass through unchanged.

    Pure function. The input list is not mutated. Output is a new list
    of frozen :class:`Token` instances.

    Idempotent: rewritten numeric tokens have ``is_numeric_like=True``
    and so are skipped on a second invocation; synthetic ``$`` tokens
    have ``is_currency_symbol=True`` and so are skipped too.
    """
    out: list[Token] = []
    for tok in tokens:
        if tok.is_numeric_like or tok.is_currency_symbol:
            out.append(tok)
            continue

        value, had_suffix = cjk_to_decimal(tok.text)
        if value is None:
            out.append(tok)
            continue

        numeric = Token(
            text=tok.text,
            normalized=str(value),
            is_currency_symbol=False,
            is_numeric_like=True,
            span=tok.span,
        )
        if had_suffix:
            # Synthetic ``$`` predecessor with a zero-width span at the
            # chunk start. Lets the existing amount scorer treat the
            # rewritten numeric as AUD-tier without any new branch.
            synthetic_dollar = Token(
                text="$",
                normalized="",
                is_currency_symbol=True,
                is_numeric_like=False,
                span=(tok.span[0], tok.span[0]),
            )
            out.append(synthetic_dollar)
        out.append(numeric)

    return out
