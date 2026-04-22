"""Phase 2 Task T-I: payment-method extractor for the expense-string parser.

Scans a token stream for one of a small, fixed set of EN + zh
payment-method keywords and returns the corresponding
:class:`~app.models.PaymentMethod`. Pure, synchronous function — no DB,
no I/O, no mutation of the input list.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. :func:`extract_payment_method` is a pure synchronous function
   consuming ``tokens`` read-only. It never constructs or touches a
   ``ParsePartial``; the orchestrator (T-K) does that.
2. Returns a bare :class:`~app.models.PaymentMethod` — NOT a narrow
   dataclass — because payment method carries no confidence score in
   Phase 2. Per the plan: "Payment method does not contribute to review
   triggers." The orchestrator simply stores whatever this returns in
   the ``ParsePartial``.
3. Matching rule: scan tokens in order, skipping currency + numeric
   tokens. The FIRST token whose ``normalized`` form appears in either
   keyword set wins — cash or transfer, whichever comes first.
4. Keyword sets are intentionally narrow:

   * ``cash``   — ``cash``, ``现金``
   * ``transfer`` — ``transfer``, ``eft``, ``bank``, ``转账``, ``银行``

   Noisier signals like ``paid`` are deliberately excluded: someone
   writing "paid cash" should resolve to :attr:`PaymentMethod.cash`,
   and with first-match-wins semantics a ``paid`` keyword would
   pre-empt the actual ``cash`` token and produce the wrong answer.

5. Tokens arrive pre-normalized at ``token.normalized`` (NFKC +
   casefold + punctuation-stripped, see
   :func:`app.core.text.normalize_alias`). The keyword sets below are
   the expected post-normalization forms, so lookup is a single
   :class:`frozenset` membership check per token.
"""

from __future__ import annotations

from app.models import PaymentMethod
from app.services.parser.tokens import Token

# Post-normalize_alias forms. Case-folded, NFKC-normalized, punctuation
# stripped — the same form tokens carry in ``token.normalized``.
_CASH_KEYWORDS: frozenset[str] = frozenset({"cash", "现金"})
_TRANSFER_KEYWORDS: frozenset[str] = frozenset({"transfer", "eft", "bank", "转账", "银行"})


def extract_payment_method(tokens: list[Token]) -> PaymentMethod:
    """Infer the payment method from the token stream.

    Returns the first recognised keyword's payment method, or
    :attr:`PaymentMethod.unknown` if no keyword is found. This stage
    does NOT emit a confidence score — payment method is never a
    review trigger in Phase 2 (plan: "Payment method does not
    contribute to review triggers"). The orchestrator simply stores
    whatever this returns in the ``ParsePartial``.

    Matching rule
    -------------
    1. Skip currency-symbol and numeric-like tokens (they can never be
       payment-method keywords).
    2. For each remaining token in order, check ``token.normalized``
       against the cash and transfer keyword sets. The FIRST match
       wins:

       * hit in ``_CASH_KEYWORDS``     → :attr:`PaymentMethod.cash`
       * hit in ``_TRANSFER_KEYWORDS`` → :attr:`PaymentMethod.transfer`

    3. No match anywhere in the stream → :attr:`PaymentMethod.unknown`.

    First-match-wins means input order matters: ``"cash transfer"``
    returns ``cash`` while ``"transfer cash"`` returns ``transfer``.

    Pure function. Does not touch the DB or ``ParsePartial``; does not
    mutate ``tokens``.
    """
    for tok in tokens:
        if tok.is_currency_symbol or tok.is_numeric_like:
            continue
        if tok.normalized in _CASH_KEYWORDS:
            return PaymentMethod.cash
        if tok.normalized in _TRANSFER_KEYWORDS:
            return PaymentMethod.transfer
    return PaymentMethod.unknown
