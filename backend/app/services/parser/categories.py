"""Phase 2 Task T-H: category matcher for the expense-string parser.

Looks up the most likely builder :class:`~app.models.category.Category`
for a parsed expense by counting keyword hits per category across the
token stream. Keywords live in
:mod:`app.services.parser.category_keywords` (a static EN + zh list
derived from the Phase 2 plan); this module is the thin glue that joins
that catalogue to the live ``categories`` table.

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. :func:`match_category` is an **async** function (DB I/O is required
   to translate the winning ``category_name`` back into a
   ``category_id``) but otherwise obeys the stage-function contract: it
   consumes ``tokens`` read-only and returns a narrow
   :class:`CategoryMatch`. It never constructs or touches a
   ``ParsePartial``; the orchestrator (T-K) does that.
2. The matcher does not mutate ``tokens`` (they are frozen) and does
   not reorder the list. The ``AsyncSession`` is used only for reads
   (a single ``SELECT Category``) — no flush, no commit, no add.
3. Only ``is_active = True`` categories ever win. If admins have
   archived the winning category the matcher returns the no-match
   result (``confidence=0.0``); the orchestrator can then surface
   review if needed.
4. Confidence tiers:
   - ``0.95`` — 2+ keyword matches mapping to the same category
   - ``0.85`` — exactly 1 keyword match
   - ``0.0``  — no match, or multiple categories tied at the top count
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category
from app.services.parser.category_keywords import CATEGORY_KEYWORDS
from app.services.parser.tokens import Token

# Confidence tiers — named here so the tests pin them, not just magic
# numbers scattered through the function.
_CONF_MULTI = 0.95
_CONF_SINGLE = 0.85
_CONF_NONE = 0.0


@dataclass(frozen=True)
class CategoryMatch:
    """Result of the category-matching stage.

    - ``category_id``: UUID of matched active category, or None.
    - ``confidence``: ``0.95`` for 2+ keywords mapping to the same
      category, ``0.85`` for exactly 1 keyword match, ``0.0`` for none
      or ambiguous (multiple categories tied at the top count).
    - ``matched_keywords``: tuple of the actual (normalised) keywords
      from the input that drove the match. For diagnostics / review
      rendering. Empty tuple when ``confidence == 0.0``.
    """

    category_id: uuid.UUID | None
    confidence: float
    matched_keywords: tuple[str, ...] = ()


def _count_keyword_hits(
    tokens: list[Token],
) -> dict[str, list[str]]:
    """For each category, collect the list of keywords hit by tokens.

    Currency + numeric tokens are skipped (they never match a category
    keyword). Returns a dict keyed by ``category_name``; each value is
    the list of token-normal strings that hit, in tokenisation order.
    A token that matches keywords in multiple categories contributes
    to every matched category — the caller decides how to break ties.
    """
    hits: dict[str, list[str]] = {name: [] for name in CATEGORY_KEYWORDS}
    for tok in tokens:
        if tok.is_currency_symbol or tok.is_numeric_like:
            continue
        if not tok.normalized:
            continue
        for category_name, keywords in CATEGORY_KEYWORDS.items():
            if tok.normalized in keywords:
                hits[category_name].append(tok.normalized)
    return hits


async def match_category(tokens: list[Token], db: AsyncSession) -> CategoryMatch:
    """Pick the category that strictly dominates the token stream.

    Pure w.r.t. inputs: ``tokens`` is consumed read-only and the
    ``AsyncSession`` is used only for ``SELECT``. Returns a
    :class:`CategoryMatch`; never constructs a ``ParsePartial``.

    Strategy:

    1. Count keyword hits per category via
       :func:`_count_keyword_hits`.
    2. Pick the category with **strictly more** hits than any other.
       Ties (two categories with the same top count) → no winner,
       confidence 0.0.
    3. Translate the winning ``category_name`` to a live
       ``category_id`` by looking it up among active categories (one
       DB query for the whole catalogue). If the category is inactive
       or missing, return no match.
    4. Confidence: 0.95 for 2+ hits, 0.85 for exactly 1 hit.
    """
    hits = _count_keyword_hits(tokens)
    # Only keep categories that actually had at least one hit.
    scored = {name: kws for name, kws in hits.items() if kws}

    if not scored:
        return CategoryMatch(
            category_id=None,
            confidence=_CONF_NONE,
            matched_keywords=(),
        )

    top_count = max(len(kws) for kws in scored.values())
    top_categories = [name for name, kws in scored.items() if len(kws) == top_count]

    if len(top_categories) > 1:
        # Tied at the top — no single category wins.
        return CategoryMatch(
            category_id=None,
            confidence=_CONF_NONE,
            matched_keywords=(),
        )

    winning_name = top_categories[0]
    winning_keywords = tuple(scored[winning_name])

    # One DB query for the whole catalogue — the active-category map
    # is tiny (23 rows) so we just fetch them all and look up by name.
    stmt = select(Category).where(Category.is_active.is_(True))
    active_categories = (await db.execute(stmt)).scalars().all()
    by_name: dict[str, uuid.UUID] = {
        cat.category_name: cat.category_id for cat in active_categories
    }

    category_id = by_name.get(winning_name)
    if category_id is None:
        # The winning name is not in the active catalogue (admins
        # archived it, or the keywords dict drifted from the seed).
        # Surface as a no-match — the review queue will still catch
        # the expense via the low overall confidence.
        return CategoryMatch(
            category_id=None,
            confidence=_CONF_NONE,
            matched_keywords=(),
        )

    confidence = _CONF_MULTI if top_count >= 2 else _CONF_SINGLE
    return CategoryMatch(
        category_id=category_id,
        confidence=confidence,
        matched_keywords=winning_keywords,
    )
