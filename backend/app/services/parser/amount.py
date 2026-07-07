"""Phase 2 Task T-F: amount extractor for the expense-string parser.

Picks the best amount candidate from a list of :class:`Token` (as
produced by :mod:`app.services.parser.tokens`). Pure function — no DB,
no I/O, no mutation of the input list.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. :func:`extract_amount` is a pure function returning an
   :class:`AmountMatch`. It never constructs or touches a
   ``ParsePartial``; the orchestrator (T-K) does that.
2. The extractor consumes ``tokens`` read-only. It does not mutate
   token instances (they are frozen) and does not reorder the list.
3. The extractor applies the Phase 2 currency-handling rules:

   =====================================================  ==========
   Input pattern                                          Confidence
   =====================================================  ==========
   ``$###.##`` exact (AUD, decimal present)               1.0
   ``$###``  (AUD, no decimal)                            0.9
   Bare number with cents (e.g. ``305.00``)               0.7
   Bare integer (e.g. ``305``)                            0.5
   Number adjacent to a non-AUD currency symbol           0.3
   No candidate / multiple equally ranked candidates      0.0
   =====================================================  ==========

4. A non-AUD currency prefix (``¥ € £ ₩ ₹``) sets
   ``unsupported_currency=True`` on the resulting match. The value is
   still populated so the admin has a number to review, but downstream
   code MUST NOT treat it as AUD — the review queue decides.

5. When multiple candidates tie for the top confidence tier the
   extractor returns ``ambiguous=True`` with confidence ``0.0``; the
   first tied candidate's value is returned so callers have a useful
   default, but the 0.0 confidence pushes the expense to review.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.parser.tokens import Token

# Confidence tiers. Names mirror the plan table for readability.
_CONF_AUD_DECIMAL = 1.0
_CONF_AUD_INTEGER = 0.9
_CONF_BARE_DECIMAL = 0.7
_CONF_BARE_INTEGER = 0.5
_CONF_UNSUPPORTED = 0.3
_CONF_NONE = 0.0


@dataclass(frozen=True)
class AmountMatch:
    """Result of the amount extraction stage.

    - ``value``: the extracted amount as a Decimal, or ``None`` if no
      candidate was found.
    - ``confidence``: 0.0 – 1.0 score reflecting how certain the
      extractor is that ``value`` is the intended amount.
    - ``unsupported_currency``: ``True`` iff the amount candidate was
      adjacent to a non-AUD currency symbol (``¥ € £ ₩ ₹``). When
      ``True``, the orchestrator fires the ``unsupported_currency``
      review reason. ``value`` may still be populated so the admin has
      a starting number during review — but downstream code MUST NOT
      treat the value as AUD; the admin decides in the queue.
    - ``source_span``: ``(start, end)`` offsets into the original raw
      text that produced this match. ``None`` when ``value`` is
      ``None``.
    - ``ambiguous``: ``True`` iff multiple candidates were found with
      the same top confidence. In that case ``value`` holds the first
      one for a useful default but the confidence is reduced to 0.0.
    """

    value: Decimal | None
    confidence: float
    unsupported_currency: bool
    source_span: tuple[int, int] | None
    ambiguous: bool


# Internal candidate record — scored before we pick a winner. Kept
# module-private so the public API is just :class:`AmountMatch`.
# Minus-sign glyphs that mark a negative / refund amount. The full-width
# form (U+FF0D) is already folded to ASCII ``-`` by the tokenizer, so we
# only need the ASCII hyphen-minus, the true Unicode minus, and the small
# hyphen-minus here.
_MINUS_SIGNS: frozenset[str] = frozenset({"-", "−", "﹣"})


def _is_negative_sign(tok: Token | None) -> bool:
    """True iff ``tok`` is a lone minus sign (not part of a number)."""
    return tok is not None and not tok.is_numeric_like and tok.text in _MINUS_SIGNS


@dataclass(frozen=True)
class _Candidate:
    value: Decimal
    confidence: float
    unsupported_currency: bool
    span: tuple[int, int]
    is_aud: bool
    negative: bool


def _score_candidate(
    numeric_token: Token,
    prev_token: Token | None,
    *,
    is_negative: bool,
) -> _Candidate:
    """Score a single numeric-like token as a candidate.

    Looks at the immediate predecessor for a currency prefix:

    * ``$`` → AUD (conf 0.9 / 1.0 depending on decimal point)
    * other recognised currency symbol → unsupported (conf 0.3)
    * no currency prefix → bare (conf 0.5 / 0.7)

    ``is_negative`` (computed by the caller from the tokens preceding the
    amount) marks a refund / negative amount, which the product does not
    support; the caller routes any winning negative candidate to review
    rather than silently booking a positive cost.

    ``Decimal(numeric_token.normalized)`` is guaranteed to parse
    because the tokenizer only sets ``is_numeric_like=True`` for
    strings that match its numeric regex; any failure here is a bug
    we want to surface.
    """
    value = Decimal(numeric_token.normalized)
    has_decimal_point = "." in numeric_token.text

    is_aud = False
    if prev_token is not None and prev_token.is_currency_symbol:
        if prev_token.text == "$":
            confidence = _CONF_AUD_DECIMAL if has_decimal_point else _CONF_AUD_INTEGER
            unsupported = False
            is_aud = True
        else:
            # Any recognised non-AUD currency symbol — ¥ € £ ₩ ₹.
            confidence = _CONF_UNSUPPORTED
            unsupported = True
    else:
        confidence = _CONF_BARE_DECIMAL if has_decimal_point else _CONF_BARE_INTEGER
        unsupported = False

    return _Candidate(
        value=value,
        confidence=confidence,
        unsupported_currency=unsupported,
        span=numeric_token.span,
        is_aud=is_aud,
        negative=is_negative,
    )


def extract_amount(tokens: list[Token]) -> AmountMatch:
    """Pick the best amount candidate from ``tokens``.

    Pure function. Does NOT consume/mutate ``tokens``. Does not hit the
    DB. Follows the Phase 2 currency-handling rule: a bare number or a
    ``$``-prefixed number is AUD with varying confidence; a non-AUD
    currency prefix yields ``unsupported_currency=True`` and a low
    confidence.

    If multiple candidates tie for the top confidence tier, the first
    tied candidate's value is returned with ``ambiguous=True`` and
    ``confidence=0.0`` so the expense is routed to manual review.
    """
    # Collect every numeric-like token as a scored candidate.
    candidates: list[_Candidate] = []
    for idx, tok in enumerate(tokens):
        if not tok.is_numeric_like:
            continue
        prev = tokens[idx - 1] if idx > 0 else None
        prev_prev = tokens[idx - 2] if idx > 1 else None
        # A leading minus attaches to the number directly (``-50``) or to
        # its currency symbol (``-$50`` → tokens ``- $ 50``).
        negative = _is_negative_sign(prev) or (
            prev is not None
            and prev.is_currency_symbol
            and _is_negative_sign(prev_prev)
        )
        candidates.append(_score_candidate(tok, prev, is_negative=negative))

    if not candidates:
        return AmountMatch(
            value=None,
            confidence=_CONF_NONE,
            unsupported_currency=False,
            source_span=None,
            ambiguous=False,
        )

    # Two or more DISTINCT explicitly-``$``-marked values in one string
    # (e.g. a unit price AND a line total) are inherently ambiguous — the
    # rules layer cannot know which is the expense. Route to review rather
    # than silently letting the higher confidence tier win. A bare
    # quantity (``20 bags``) is not ``$``-marked, so it does not trip this.
    aud_values = {c.value for c in candidates if c.is_aud}
    if len(aud_values) > 1:
        winner = max((c for c in candidates if c.is_aud), key=lambda c: c.confidence)
        return AmountMatch(
            value=winner.value,
            confidence=_CONF_NONE,
            unsupported_currency=winner.unsupported_currency,
            source_span=winner.span,
            ambiguous=True,
        )

    # Find the top confidence tier and all candidates at that tier.
    top_confidence = max(c.confidence for c in candidates)
    top_tier = [c for c in candidates if c.confidence == top_confidence]

    if len(top_tier) > 1:
        # Tie — the first tied candidate's value is returned as a
        # useful default, but confidence collapses to 0.0 so the
        # orchestrator treats this as ambiguous.
        winner = top_tier[0]
        return AmountMatch(
            value=winner.value,
            confidence=_CONF_NONE,
            unsupported_currency=winner.unsupported_currency,
            source_span=winner.span,
            ambiguous=True,
        )

    winner = top_tier[0]
    if winner.negative:
        # Negative / refund amount — unsupported by the product. Surface a
        # positive default for the reviewer but force review rather than
        # silently flipping the sign and booking it as a cost.
        return AmountMatch(
            value=winner.value,
            confidence=_CONF_NONE,
            unsupported_currency=winner.unsupported_currency,
            source_span=winner.span,
            ambiguous=True,
        )
    return AmountMatch(
        value=winner.value,
        confidence=winner.confidence,
        unsupported_currency=winner.unsupported_currency,
        source_span=winner.span,
        ambiguous=False,
    )
