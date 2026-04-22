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
    """Peel a leading currency symbol off a whitespace-separated chunk.

    If ``chunk`` starts with a recognised currency symbol AND has more
    characters after it, emit the currency symbol as its own token and
    recurse on the remainder (to handle the degenerate ``"$$305"`` case
    symmetrically). A bare currency symbol with no trailing amount
    becomes a single currency-symbol token.

    Empty input is unreachable by construction — callers split on
    whitespace runs and filter empties — but we still return ``[]``
    defensively.
    """
    if not chunk:
        return []

    first = chunk[0]
    if first in _CURRENCY_SYMBOLS and len(chunk) > 1:
        sym_token = _build_token(first, start, start + 1)
        rest = chunk[1:]
        return [sym_token, *_peel_currency(rest, start + 1)]

    return [_build_token(chunk, start, start + len(chunk))]


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
