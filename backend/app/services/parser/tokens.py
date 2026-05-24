"""Phase 2 Task T-E: tokenizer for the expense-string parser.

The rules-pass pipeline starts here: :func:`tokenize` splits a raw
natural-language expense string (e.g. ``"$305 Bunnings Kelly
bluemetal"``) into a list of :class:`Token` objects that the downstream
stage functions (amount extraction, job matching, supplier matching,
category matching, payment-method detection) consume.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. Pure function — no DB, no I/O, no side effects.
2. Returns a narrow stage-specific result (a ``list[Token]``); the
   orchestrator (landing in T-K) is the sole constructor of
   :class:`~app.services.parser.llm_adapter.ParsePartial`.
3. :class:`Token` is ``frozen=True`` + hashable, so downstream stages
   can safely use tokens as dict keys / set members without worrying
   about accidental mutation.
4. ``text`` preserves the original slice from the input (case +
   currency symbols); ``normalized`` is the form downstream alias
   matchers consume (via :func:`app.core.text.normalize_alias`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize_alias

# Explicit set — matching is by exact single-character identity, not a
# broader Unicode-currency regex. Keeps behaviour auditable.
_CURRENCY_SYMBOLS: frozenset[str] = frozenset({"$", "¥", "€", "£", "₩", "₹"})

# A numeric-like token is either a plain decimal (``305`` / ``305.00``)
# or a comma-grouped decimal (``1,234`` / ``1,234.56``). Anything with
# non-digit / non-separator characters is not numeric-like.
_NUMERIC_RE = re.compile(r"^(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)$")


@dataclass(frozen=True)
class Token:
    """One token from the raw input.

    - ``text`` is the original slice from the input (preserves case +
      the actual characters the user typed, including currency symbols).
    - ``normalized`` is the NFKC + casefold + punctuation-stripped form
      used by downstream alias matchers (via ``normalize_alias`` in
      ``app.core.text``). For currency-symbol tokens it is ``""``; for
      numeric-like tokens it is the raw digit string with commas
      stripped (``"1,234.56"`` → ``"1234.56"``).
    - ``is_currency_symbol`` is True iff the token is exactly one of
      the recognised currency symbols (``$``, ``¥``, ``€``, ``£``,
      ``₩``, ``₹``). The amount extractor uses this to tell AUD from
      unsupported currencies.
    - ``is_numeric_like`` is True iff the token matches a number-ish
      pattern (digits, optionally with decimal point or comma
      separators). The amount extractor uses this as the primary
      candidate filter.
    - ``span`` is the ``(start, end)`` offset pair into the original
      ``raw_text`` — useful for "why did the parser pick this?"
      diagnostics and for building descriptions from unmatched tokens.
    """

    text: str
    normalized: str
    is_currency_symbol: bool
    is_numeric_like: bool
    span: tuple[int, int]


def _build_token(text: str, start: int, end: int) -> Token:
    """Construct a :class:`Token` for a single non-empty chunk.

    Computes ``is_currency_symbol`` / ``is_numeric_like`` from ``text``
    and derives ``normalized`` accordingly:

    - currency symbol → ``""`` (not a word)
    - numeric-like → raw digits with commas stripped
    - otherwise → :func:`normalize_alias` of the raw text
    """
    is_currency = text in _CURRENCY_SYMBOLS
    is_numeric = bool(_NUMERIC_RE.match(text))
    if is_currency:
        normalized = ""
    elif is_numeric:
        normalized = text.replace(",", "")
    else:
        normalized = normalize_alias(text)
    return Token(
        text=text,
        normalized=normalized,
        is_currency_symbol=is_currency,
        is_numeric_like=is_numeric,
        span=(start, end),
    )


def _peel_currency(chunk: str, start: int) -> list[Token]:
    """Split a whitespace-separated chunk around every currency symbol.

    Currency symbols (``$ ¥ € £ ₩ ₹``) act as token boundaries even
    when they appear mid-chunk with no surrounding whitespace, so:

    - ``"$305"``              -> ``["$", "305"]`` (leading)
    - ``"305$"``              -> ``["305", "$"]`` (trailing)
    - ``"电工$100"``            -> ``["电工", "$", "100"]`` (mid-chunk —
      operator-reported gap: CJK words concatenated to a $-prefixed
      number with no space used to tokenize as one weird chunk and
      bypassed both the amount and job matchers entirely)
    - ``"$$305"``             -> ``["$", "$", "305"]`` (degenerate)

    Spans are computed from the original ``start`` offset so downstream
    diagnostics + the amount-source-span exclusion in the job matcher
    still point at the right slice of ``raw_text``.

    Known limitation (acceptable per scope): only currency symbols
    are token boundaries. A digit/letter boundary inside a chunk does
    NOT split, so ``"电工$100水工$200"`` (two amounts concatenated in
    one chunk with no whitespace) tokenises as
    ``["电工", "$", "100水工", "$", "200"]``. The "100水工" token is
    not amount-like (mixed letters) so the second amount wins. This
    is rare in practice because the operator workflow uses newlines
    between items, which whitespace-split first. Adding a
    digit/letter split would also break legitimate alphanumeric job
    codes like ``"KH-01"`` and is deliberately avoided.

    Empty input is unreachable by construction — callers split on
    whitespace runs and filter empties — but we still return ``[]``
    defensively.
    """
    if not chunk:
        return []

    tokens: list[Token] = []
    buffer: list[str] = []
    buffer_start = start

    for offset, ch in enumerate(chunk):
        absolute = start + offset
        if ch in _CURRENCY_SYMBOLS:
            # Flush any accumulated non-currency characters as one token.
            if buffer:
                segment = "".join(buffer)
                tokens.append(
                    _build_token(segment, buffer_start, buffer_start + len(segment))
                )
                buffer = []
            # Emit the currency symbol as its own single-character token.
            tokens.append(_build_token(ch, absolute, absolute + 1))
            buffer_start = absolute + 1
        else:
            if not buffer:
                buffer_start = absolute
            buffer.append(ch)

    if buffer:
        segment = "".join(buffer)
        tokens.append(
            _build_token(segment, buffer_start, buffer_start + len(segment))
        )

    return tokens


def tokenize(raw_text: str) -> list[Token]:
    """Split ``raw_text`` into tokens ready for downstream stages.

    Splits on runs of Unicode whitespace (spaces, tabs, newlines).
    Currency symbols adjacent to a number are emitted as separate
    tokens (so ``"$305"`` yields two tokens: a currency symbol ``$``
    and a numeric-like ``305``). This lets the amount extractor match
    currency+number as a pair without regex-ing the raw string.

    Returns an empty list for empty or whitespace-only input.

    The returned tokens' ``span`` fields track offsets into the
    original ``raw_text``, so downstream diagnostics can map a token
    back to its source position.
    """
    if not raw_text or not raw_text.strip():
        return []

    tokens: list[Token] = []
    # Use finditer on non-whitespace runs so we get accurate spans into
    # the original string. ``\S+`` matches any run of non-whitespace,
    # preserving original character boundaries.
    for match in re.finditer(r"\S+", raw_text):
        chunk = match.group(0)
        chunk_start = match.start()
        tokens.extend(_peel_currency(chunk, chunk_start))

    return tokens
