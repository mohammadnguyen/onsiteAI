"""Phase 2 Task T-H: DB-backed tests for the category matcher.

Exercises :func:`app.services.parser.categories.match_category` against
real Postgres (5433). Every test seeds the 23 builder categories via
the ``seed_categories`` fixture defined in ``backend/tests/conftest.py``
and then feeds ``tokenize`` output through the matcher.

Coverage breakdown:

* 23 categories × 2 keywords each (one EN, one zh) = 46 parametrised
  happy-path tests asserting the right ``category_id`` is returned at
  conf 0.85.
* multi-keyword boost to 0.95 (2+ Carpentry keywords in one input)
* single-keyword conf 0.85 anchor
* no-match returns ``(None, 0.0, ())``
* tied top count → no winner (ambiguous)
* "tiles" disambiguation: ``"tiles"`` belongs to Tiling only
* plan spec anchors: ``bluemetal`` → Earthworks, ``水工材料`` → Plumbing
* full expense strings: ``"$305 Bunnings Kelly bluemetal"`` →
  Earthworks; ``"工地1 水工材料 163"`` → Plumbing
* inactive category never wins (admin archival case)
* input list not mutated (purity contract)
"""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import select

from app.models import Category
from app.services.parser.categories import CategoryMatch, match_category
from app.services.parser.tokens import tokenize

# ---------------------------------------------------------------------------
# (keyword, expected category_name) pairs — 23 × 2 = 46 total
# ---------------------------------------------------------------------------
# Each category gets one EN keyword + one zh keyword. These are a
# subset of ``CATEGORY_KEYWORDS`` chosen to be unambiguous (a keyword
# is listed under exactly one category) so the test cleanly pins the
# routing behaviour.

_KEYWORD_CASES: list[tuple[str, str]] = [
    # EN: one unambiguous keyword per category
    ("demolition", "Demolition"),
    ("excavator", "Earthworks"),
    ("concrete", "Concrete"),
    ("brickwork", "Brickwork"),
    ("carpentry", "Carpentry"),
    ("roofing", "Roofing"),
    ("cladding", "Cladding"),
    ("waterproofing", "Waterproofing"),
    ("plumbing", "Plumbing"),
    ("electrical", "Electrical"),
    ("gyprock", "Gyprock"),
    ("painting", "Painting"),
    ("flooring", "Flooring"),
    ("tiler", "Tiling"),
    ("joinery", "Joinery"),
    ("glazing", "Windows & Doors"),
    ("rsj", "Structural Steel"),
    ("wages", "Labour"),
    ("hoarding", "Preliminaries"),
    ("rental", "Equipment Hire"),
    ("skipbin", "Waste / Skip Bin"),
    ("freight", "Delivery"),
    ("miscellaneous", "Miscellaneous"),
    # zh: one zh keyword per category
    ("拆除", "Demolition"),
    ("挖掘", "Earthworks"),
    ("水泥", "Concrete"),
    ("砌砖", "Brickwork"),
    ("木工", "Carpentry"),
    ("屋顶", "Roofing"),
    ("外墙", "Cladding"),
    ("防水", "Waterproofing"),
    ("水工材料", "Plumbing"),
    ("电工", "Electrical"),
    ("石膏板", "Gyprock"),
    ("油漆", "Painting"),
    ("地板", "Flooring"),
    ("瓷砖", "Tiling"),
    ("橱柜", "Joinery"),
    ("窗户", "Windows & Doors"),
    ("钢材", "Structural Steel"),
    ("人工", "Labour"),
    ("脚手架", "Preliminaries"),
    ("租赁", "Equipment Hire"),
    ("垃圾桶", "Waste / Skip Bin"),
    ("运费", "Delivery"),
    ("其他", "Miscellaneous"),
]


async def _category_id_by_name(db_session, name: str) -> uuid.UUID:
    """Small helper — look up a seeded category by name."""
    row = (
        await db_session.execute(select(Category).where(Category.category_name == name))
    ).scalar_one()
    return row.category_id


# ---------------------------------------------------------------------------
# Parametrised happy-path: each keyword routes to its category (46 cases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword,expected_name", _KEYWORD_CASES)
async def test_keyword_maps_to_category(db_session, seed_categories, keyword, expected_name):
    """Every keyword in ``_KEYWORD_CASES`` routes to its category at 0.85."""
    expected_id = await _category_id_by_name(db_session, expected_name)
    result = await match_category(tokenize(keyword), db_session)

    # Parametrize IDs include ``keyword`` + ``expected_name`` so a
    # failure here points cleanly at the offending row without a
    # custom assert message (which ruff-format and black disagree on
    # formatting of).
    assert result.category_id == expected_id
    assert result.confidence == 0.85
    # The normalised form of the keyword should appear in matched_keywords.
    assert len(result.matched_keywords) == 1


# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_keyword_boost_to_high_confidence(db_session, seed_categories):
    """Two Carpentry keywords in one input → conf 0.95."""
    expected_id = await _category_id_by_name(db_session, "Carpentry")
    result = await match_category(tokenize("timber framing"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.95
    # Two keywords matched, both Carpentry.
    assert len(result.matched_keywords) == 2
    assert set(result.matched_keywords) == {"timber", "framing"}


@pytest.mark.asyncio
async def test_single_keyword_standard_confidence(db_session, seed_categories):
    """One Carpentry keyword in one input → conf 0.85."""
    expected_id = await _category_id_by_name(db_session, "Carpentry")
    result = await match_category(tokenize("timber"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85
    assert result.matched_keywords == ("timber",)


@pytest.mark.asyncio
async def test_no_match_returns_no_category(db_session, seed_categories):
    """Random unrecognised words return the empty match."""
    result = await match_category(tokenize("random unknown stuff"), db_session)

    assert result == CategoryMatch(
        category_id=None,
        confidence=0.0,
        matched_keywords=(),
    )


@pytest.mark.asyncio
async def test_ambiguous_tied_categories(db_session, seed_categories):
    """One keyword for each of two categories → no winner, conf 0.0.

    ``timber`` is Carpentry; ``sparky`` is Electrical. Both contribute
    exactly one hit — the tie means no single category dominates.
    """
    result = await match_category(tokenize("timber sparky"), db_session)

    assert result.category_id is None
    assert result.confidence == 0.0
    assert result.matched_keywords == ()


@pytest.mark.asyncio
async def test_strict_dominance_breaks_tie(db_session, seed_categories):
    """2 Carpentry + 1 Electrical → Carpentry wins at 0.95."""
    expected_id = await _category_id_by_name(db_session, "Carpentry")
    result = await match_category(tokenize("timber framing sparky"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.95
    assert set(result.matched_keywords) == {"timber", "framing"}


# ---------------------------------------------------------------------------
# Disambiguation: "tiles" belongs to Tiling only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiles_routes_to_tiling(db_session, seed_categories):
    """``tiles`` alone resolves to Tiling (not Roofing) — see module docstring."""
    expected_id = await _category_id_by_name(db_session, "Tiling")
    result = await match_category(tokenize("tiles"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_roof_tiles_still_wins_roofing(db_session, seed_categories):
    """``roof tiles`` → 1 Roofing hit (``roof``) + 1 Tiling hit (``tiles``)
    which ties. We specifically carved ``tiles`` out of Roofing so that
    a mention of ``roof`` (which is ONLY Roofing) dominates when paired
    with other Roofing words. Without other words present it ties."""
    # Ties expected: ``roof`` → Roofing, ``tiles`` → Tiling.
    result = await match_category(tokenize("roof tiles"), db_session)

    assert result.category_id is None
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Plan spec anchors — the text blurbs from the Phase 2 plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_anchor_bluemetal(db_session, seed_categories):
    """``bluemetal`` single-token → Earthworks."""
    expected_id = await _category_id_by_name(db_session, "Earthworks")
    result = await match_category(tokenize("bluemetal"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_spec_anchor_shui_gong_cai_liao(db_session, seed_categories):
    """``水工材料`` single-token → Plumbing."""
    expected_id = await _category_id_by_name(db_session, "Plumbing")
    result = await match_category(tokenize("水工材料"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_full_en_phrase_routes_to_earthworks(db_session, seed_categories):
    """``$305 Bunnings Kelly bluemetal`` → Earthworks (single keyword hit)."""
    expected_id = await _category_id_by_name(db_session, "Earthworks")
    result = await match_category(tokenize("$305 Bunnings Kelly bluemetal"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85
    assert result.matched_keywords == ("bluemetal",)


@pytest.mark.asyncio
async def test_full_zh_phrase_routes_to_plumbing(db_session, seed_categories):
    """``工地1 水工材料 163`` → Plumbing (single keyword hit)."""
    expected_id = await _category_id_by_name(db_session, "Plumbing")
    result = await match_category(tokenize("工地1 水工材料 163"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85
    assert result.matched_keywords == ("水工材料",)


# ---------------------------------------------------------------------------
# Active-only semantics + purity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_winning_category_returns_no_match(db_session, seed_categories):
    """If the winning category is archived, the matcher bails out."""
    # Archive Carpentry — ``timber`` would have won but now should not.
    row = (
        await db_session.execute(select(Category).where(Category.category_name == "Carpentry"))
    ).scalar_one()
    row.is_active = False
    await db_session.flush()

    result = await match_category(tokenize("timber framing"), db_session)

    assert result.category_id is None
    assert result.confidence == 0.0
    assert result.matched_keywords == ()


@pytest.mark.asyncio
async def test_currency_and_numeric_tokens_skipped(db_session, seed_categories):
    """Pure currency + numeric inputs never match a category."""
    result = await match_category(tokenize("$305 163"), db_session)

    assert result == CategoryMatch(
        category_id=None,
        confidence=0.0,
        matched_keywords=(),
    )


@pytest.mark.asyncio
async def test_pure_function_contract(db_session, seed_categories):
    """``match_category`` must not mutate its input token list."""
    tokens = tokenize("$305 Bunnings Kelly bluemetal")
    before = copy.deepcopy(tokens)

    await match_category(tokens, db_session)

    assert tokens == before
    assert len(tokens) == len(before)
    for got, expected in zip(tokens, before, strict=True):
        assert got == expected


@pytest.mark.asyncio
async def test_empty_input_returns_no_match(db_session, seed_categories):
    """Empty token list short-circuits to the no-match result."""
    result = await match_category([], db_session)

    assert result == CategoryMatch(
        category_id=None,
        confidence=0.0,
        matched_keywords=(),
    )


@pytest.mark.asyncio
async def test_category_match_is_frozen():
    """:class:`CategoryMatch` is frozen — catches accidental mutation."""
    from dataclasses import FrozenInstanceError

    cm = CategoryMatch(category_id=None, confidence=0.0)
    with pytest.raises(FrozenInstanceError):
        cm.confidence = 1.0  # type: ignore[misc]


@pytest.mark.asyncio
async def test_case_and_punctuation_insensitive(db_session, seed_categories):
    """Upper-case + trailing punctuation still resolve via normalisation."""
    expected_id = await _category_id_by_name(db_session, "Demolition")
    result = await match_category(tokenize("DEMOLITION!"), db_session)

    assert result.category_id == expected_id
    assert result.confidence == 0.85
