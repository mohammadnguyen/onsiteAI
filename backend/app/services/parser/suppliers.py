"""Phase 2 Task T-H: supplier matcher for the expense-string parser.

Looks up the canonical :class:`~app.models.supplier.Supplier` for a
parsed expense by running each non-currency / non-numeric token through
two routes against the DB:

1. :class:`~app.models.supplier.SupplierAlias` (normalised alias lookup —
   the primary / intended route)
2. :class:`~app.models.supplier.Supplier` ``supplier_name`` (exact
   normalised match via ``supplier_normalized``)

Only ``is_active = True`` suppliers are ever returned. An inactive
supplier's aliases are ignored for parser purposes.

When no supplier / alias matches but a single capitalised (ASCII) or
non-ASCII (CJK etc.) word token is present, the matcher proposes it as
a *candidate* new supplier name with confidence 0.5 so the orchestrator
can surface a "create supplier?" review reason to the admin. Multiple
such candidates collapse to confidence 0.0 because the parser cannot
tell which one is the supplier.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. :func:`match_supplier` is an **async** function (DB I/O is required)
   but otherwise obeys the stage-function contract: it consumes
   ``tokens`` read-only and returns a narrow :class:`SupplierMatch`. It
   never constructs or touches a ``ParsePartial``; the orchestrator
   (T-K) does that.
2. The matcher does not mutate ``tokens`` (they are frozen) and does
   not reorder the list. The ``AsyncSession`` is used only for reads
   (``SELECT``) — no flush, no commit, no add.
3. Ambiguity — two or more distinct suppliers matched across the token
   stream / routes — is returned as ``confidence=0.3`` with the sorted
   UUID tuple in ``ambiguous_matches``.
4. ``matched_via`` reflects the priority order ``alias > name`` for
   unique matches. It is ``None`` for the ambiguous, candidate, and
   no-match cases so callers can't accidentally misreport a route.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_alias
from app.models import Supplier, SupplierAlias
from app.services.parser.tokens import Token

# Confidence tiers — named here so the tests pin them, not just magic
# numbers scattered through the function.
_CONF_UNIQUE = 0.95
_CONF_CANDIDATE = 0.5
_CONF_AMBIGUOUS = 0.3
_CONF_NONE = 0.0


@dataclass(frozen=True)
class SupplierMatch:
    """Result of the supplier-matching stage.

    - ``supplier_id``: matched active supplier's UUID, or None.
    - ``confidence``: ``0.95`` for 1 unique match, ``0.3`` for
      ambiguous, ``0.5`` for "0 matches but a capitalised-looking word
      is present as a candidate new supplier", ``0.0`` otherwise.
    - ``ambiguous_matches``: sorted tuple of UUIDs when 2+ matched.
    - ``candidate_supplier_name``: when confidence == 0.5, the exact
      original-case text of the capitalised token the parser wants to
      propose as a new supplier. None in all other cases.
    - ``matched_via``: ``'name'`` (via ``supplier_normalized``) or
      ``'alias'``, or None.
    """

    supplier_id: uuid.UUID | None
    confidence: float
    ambiguous_matches: tuple[uuid.UUID, ...] = ()
    candidate_supplier_name: str | None = None
    matched_via: str | None = None


def _word_tokens(tokens: list[Token]) -> list[Token]:
    """Filter out currency + numeric tokens; keep the rest in order.

    The matcher never looks at bare digits or ``$`` — suppliers are
    not named with those. Tokens whose ``normalized`` is empty (e.g.
    the currency-symbol tokens themselves) are also filtered; they
    would never hit an alias row anyway.
    """
    out: list[Token] = []
    for tok in tokens:
        if tok.is_currency_symbol or tok.is_numeric_like:
            continue
        if not tok.normalized:
            continue
        out.append(tok)
    return out


def _is_candidate_token(tok: Token) -> bool:
    """Does ``tok`` look like a proper-noun supplier name?

    A token is a candidate iff its **original text** either starts
    with an ASCII upper-case letter (``"Acme"``, ``"Bunnings"``) OR
    contains at least one non-ASCII character (covers CJK such as
    ``"邦宁"`` where case is undefined). Lower-case ASCII tokens
    (``"timber"``) are rejected — they are much more likely to be a
    category keyword than a supplier name.
    """
    if not tok.text:
        return False
    first = tok.text[0]
    if first.isascii():
        return first.isupper()
    # Any token containing a non-ASCII character — CJK, accented
    # Latin, etc. — is treated as proper-noun-like.
    return any(not ch.isascii() for ch in tok.text)


async def match_supplier(tokens: list[Token], db: AsyncSession) -> SupplierMatch:
    """Match the token stream against active suppliers.

    Pure w.r.t. inputs: ``tokens`` is consumed read-only and the
    ``AsyncSession`` is used only for ``SELECT``. Returns a
    :class:`SupplierMatch`; never constructs a ``ParsePartial``.

    Strategy (two queries, Phase 2 simplicity):

    1. One ``SELECT SupplierAlias`` filtered by
       ``alias_text_normalized IN (<token normals>)`` with the parent
       :class:`Supplier` eager-loaded; reject rows whose parent
       supplier is inactive.
    2. One ``SELECT Supplier`` filtered by ``is_active = True`` —
       iterate Python-side and compare ``normalize_alias(supplier_name)``
       (the ``supplier_normalized`` column) to each token normal.

    Across both queries and all tokens, collect the set of unique
    matching ``supplier_id`` values and bucket the route (alias / name)
    that each match came through so we can report ``matched_via`` with
    the documented priority (alias > name).
    """
    word_tokens = _word_tokens(tokens)
    if not word_tokens:
        return SupplierMatch(
            supplier_id=None,
            confidence=_CONF_NONE,
            ambiguous_matches=(),
            candidate_supplier_name=None,
            matched_via=None,
        )

    normals_set: set[str] = {tok.normalized for tok in word_tokens}

    # Track which routes matched which suppliers; priority alias > name.
    by_route: dict[str, set[uuid.UUID]] = {"alias": set(), "name": set()}
    # Track which token normals actually produced a match — used to
    # decide which tokens are "unmatched" for the candidate-proposal
    # fallback below.
    matched_normals: set[str] = set()

    # --- Route 1: alias lookup (active-only via parent Supplier) ---
    alias_stmt = (
        select(SupplierAlias)
        .where(SupplierAlias.alias_text_normalized.in_(normals_set))
        .options(selectinload(SupplierAlias.supplier))
    )
    alias_rows = (await db.execute(alias_stmt)).scalars().all()
    for alias in alias_rows:
        if alias.supplier is not None and alias.supplier.is_active:
            by_route["alias"].add(alias.supplier_id)
            matched_normals.add(alias.alias_text_normalized)

    # --- Route 2: scan active suppliers for supplier_normalized hits ---
    suppliers_stmt = select(Supplier).where(Supplier.is_active.is_(True))
    active_suppliers = (await db.execute(suppliers_stmt)).scalars().all()
    for supplier in active_suppliers:
        name_normal = normalize_alias(supplier.supplier_name)
        if name_normal and name_normal in normals_set:
            by_route["name"].add(supplier.supplier_id)
            matched_normals.add(name_normal)

    all_matches = by_route["alias"] | by_route["name"]

    if len(all_matches) == 1:
        # Exactly one unique supplier across every route. Assign
        # ``matched_via`` with the documented priority: alias first,
        # then name.
        (unique_id,) = all_matches
        for route in ("alias", "name"):
            if unique_id in by_route[route]:
                return SupplierMatch(
                    supplier_id=unique_id,
                    confidence=_CONF_UNIQUE,
                    ambiguous_matches=(),
                    candidate_supplier_name=None,
                    matched_via=route,
                )
        # Unreachable by construction (``all_matches`` is the union)
        # but keep the fallback explicit so a future refactor can't
        # silently return a ``matched_via=None`` on a unique hit.
        return SupplierMatch(
            supplier_id=unique_id,
            confidence=_CONF_UNIQUE,
            ambiguous_matches=(),
            candidate_supplier_name=None,
            matched_via=None,
        )

    if len(all_matches) >= 2:
        return SupplierMatch(
            supplier_id=None,
            confidence=_CONF_AMBIGUOUS,
            ambiguous_matches=tuple(sorted(all_matches)),
            candidate_supplier_name=None,
            matched_via=None,
        )

    # --- No matches: check for a candidate supplier name. ---
    # Gather every word-token that did NOT produce a supplier match and
    # looks proper-noun-like (capitalised ASCII OR non-ASCII). If
    # exactly one such candidate exists, propose it with conf 0.5;
    # zero or multiple candidates collapse to conf 0.0.
    unmatched_candidates = [
        tok
        for tok in word_tokens
        if tok.normalized not in matched_normals and _is_candidate_token(tok)
    ]

    if len(unmatched_candidates) == 1:
        candidate = unmatched_candidates[0]
        return SupplierMatch(
            supplier_id=None,
            confidence=_CONF_CANDIDATE,
            ambiguous_matches=(),
            candidate_supplier_name=candidate.text,
            matched_via=None,
        )

    return SupplierMatch(
        supplier_id=None,
        confidence=_CONF_NONE,
        ambiguous_matches=(),
        candidate_supplier_name=None,
        matched_via=None,
    )
