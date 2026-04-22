"""Phase 2 Task T-H: DB-backed tests for the supplier matcher.

Exercises :func:`app.services.parser.suppliers.match_supplier` against
real Postgres (5433). Every test seeds a small supplier graph — two
active suppliers with English + Chinese aliases and one inactive
supplier — then feeds ``tokenize`` output through the matcher and
asserts on the narrow :class:`SupplierMatch` result.

Tests cover:

* single alias (EN) matches + priority of the alias route over name
* alias buried inside a phrase with currency + numeric noise
* ambiguity when two tokens hit different suppliers
* inactive suppliers are never matched (aliases ignored too)
* 0-match + capitalised candidate proposal (conf 0.5)
* 0-match + multiple capitalised tokens (no single candidate → conf 0.0)
* 0-match + lowercase-only (no candidate → conf 0.0)
* currency + numeric tokens are skipped
* case + punctuation insensitivity via :func:`normalize_alias`
* CJK alias matching (non-ASCII normalisation round-trip)
* the candidate name preserves the original token text (case-sensitive)
* purity — the matcher does not mutate its input token list
"""

from __future__ import annotations

import copy
import uuid

import pytest
import pytest_asyncio

from app.models import LanguageCode, Supplier, SupplierAlias
from app.services.parser.suppliers import SupplierMatch, match_supplier
from app.services.parser.tokens import tokenize


async def _make_supplier(
    db_session,
    *,
    name: str,
    is_active: bool = True,
) -> Supplier:
    """Insert a bare :class:`Supplier` into the current transaction.

    ``supplier_normalized`` is synced automatically by the
    ``before_insert`` event listener on the model, so callers only
    supply the human-readable ``name``.
    """
    supplier = Supplier(
        supplier_id=uuid.uuid4(),
        supplier_name=name,
        is_active=is_active,
    )
    db_session.add(supplier)
    await db_session.flush()
    return supplier


@pytest_asyncio.fixture
async def seeded_suppliers(db_session):
    """Seed two active suppliers + one inactive, each with aliases.

    * Supplier A — ``Bunnings``. Aliases ``Bunnings Warehouse``,
      ``邦宁`` (a plausible Chinese rendering — lets us test CJK alias
      matching). NOTE: the supplier's own name ``Bunnings`` normalises
      to ``bunnings``, which is ALSO a possible alias normal; we do
      NOT seed ``"Bunnings"`` as an alias because
      ``supplier_aliases.alias_text_normalized`` is globally UNIQUE
      across all suppliers and the test fixture needs to be tidy.
      Matching on ``"Bunnings"`` therefore flows via the name route.
    * Supplier B — ``Mitre 10`` (normalises to ``mitre10``). Aliases
      ``Mitre`` (normalises to ``mitre``) and ``M10`` (normalises to
      ``m10``).
    * Supplier C — ``Old Hardware`` with ``is_active=False``. Alias
      ``Old`` — must NEVER produce a match.
    * Supplier D — ``Reece`` active, no aliases. Used to test name
      route + the priority-ordering edge where a token could match
      both an alias AND a name.

    Returned as a 4-tuple ``(sup_a, sup_b, sup_c, sup_d)``.
    """
    sup_a = await _make_supplier(db_session, name="Bunnings")
    db_session.add_all(
        [
            SupplierAlias(
                supplier_id=sup_a.supplier_id,
                alias_text="Bunnings Warehouse",
                language_code=LanguageCode.en,
            ),
            SupplierAlias(
                supplier_id=sup_a.supplier_id,
                alias_text="邦宁",
                language_code=LanguageCode.zh,
            ),
        ]
    )

    sup_b = await _make_supplier(db_session, name="Mitre 10")
    db_session.add_all(
        [
            SupplierAlias(
                supplier_id=sup_b.supplier_id,
                alias_text="Mitre",
                language_code=LanguageCode.en,
            ),
            SupplierAlias(
                supplier_id=sup_b.supplier_id,
                alias_text="M10",
                language_code=LanguageCode.en,
            ),
        ]
    )

    sup_c = await _make_supplier(db_session, name="Old Hardware", is_active=False)
    db_session.add(
        SupplierAlias(
            supplier_id=sup_c.supplier_id,
            alias_text="Old",
            language_code=LanguageCode.en,
        )
    )

    sup_d = await _make_supplier(db_session, name="Reece")

    await db_session.flush()
    return (sup_a, sup_b, sup_c, sup_d)


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_alias_match_en(db_session, seeded_suppliers):
    """Plain English alias → unique match via the alias route."""
    _, sup_b, *_ = seeded_suppliers
    result = await match_supplier(tokenize("Mitre"), db_session)

    assert result == SupplierMatch(
        supplier_id=sup_b.supplier_id,
        confidence=0.95,
        ambiguous_matches=(),
        candidate_supplier_name=None,
        matched_via="alias",
    )


@pytest.mark.asyncio
async def test_single_alias_match_zh(db_session, seeded_suppliers):
    """CJK alias → unique match via the alias route."""
    sup_a, *_ = seeded_suppliers
    result = await match_supplier(tokenize("邦宁"), db_session)

    assert result.supplier_id == sup_a.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"
    assert result.ambiguous_matches == ()
    assert result.candidate_supplier_name is None


@pytest.mark.asyncio
async def test_supplier_name_match(db_session, seeded_suppliers):
    """Unique match via ``supplier_normalized`` (no alias row for it)."""
    sup_a, *_ = seeded_suppliers
    result = await match_supplier(tokenize("Bunnings"), db_session)

    assert result.supplier_id == sup_a.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "name"


@pytest.mark.asyncio
async def test_name_match_in_phrase_with_noise(db_session, seeded_suppliers):
    """Supplier name buried in currency + numeric + other-word noise still matches.

    The tokenizer splits ``"Bunnings Warehouse"`` into two tokens
    (``bunnings`` + ``warehouse``); neither hits the multi-word
    ``bunningswarehouse`` alias by itself, but ``bunnings`` matches
    sup_a via the name route. Same supplier, single unique match.
    """
    sup_a, *_ = seeded_suppliers
    result = await match_supplier(
        tokenize("$305 Bunnings Warehouse bluemetal 163"),
        db_session,
    )

    assert result.supplier_id == sup_a.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "name"


@pytest.mark.asyncio
async def test_alias_route_wins_over_name_when_same_supplier(db_session, seeded_suppliers):
    """When the same supplier matches via BOTH alias and name, ``matched_via``
    reports the higher-priority route (alias)."""
    _, sup_b, *_ = seeded_suppliers
    # Seed an extra alias whose normalised form equals the seeded
    # supplier B's normalised name, so both routes hit simultaneously.
    db_session.add(
        SupplierAlias(
            supplier_id=sup_b.supplier_id,
            alias_text="Mitre-10",  # normalises to ``mitre10`` == sup_b.supplier_normalized
            language_code=LanguageCode.en,
        )
    )
    await db_session.flush()

    # A single token whose normal is ``mitre10`` hits the alias AND
    # the name route for sup_b. Priority alias > name.
    result = await match_supplier(tokenize("Mitre10"), db_session)

    assert result.supplier_id == sup_b.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


# ---------------------------------------------------------------------------
# Ambiguity, inactive, candidate proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_two_suppliers(db_session, seeded_suppliers):
    """Two tokens matching two different suppliers → ambiguous, conf 0.3."""
    sup_a, sup_b, *_ = seeded_suppliers
    result = await match_supplier(tokenize("Bunnings Mitre"), db_session)

    assert result.supplier_id is None
    assert result.confidence == 0.3
    assert result.matched_via is None
    assert result.candidate_supplier_name is None
    assert result.ambiguous_matches == tuple(sorted([sup_a.supplier_id, sup_b.supplier_id]))


@pytest.mark.asyncio
async def test_inactive_supplier_alias_not_matched(db_session, seeded_suppliers):
    """An alias on an inactive supplier must not produce a match.

    ``Old`` is supplier_c's alias but the supplier is archived.
    ``Old`` is capitalised, so the fallback candidate proposal also
    fires (single candidate) → conf 0.5.
    """
    result = await match_supplier(tokenize("Old"), db_session)

    assert result.supplier_id is None
    # Capitalised unmatched token → fallback to candidate proposal.
    assert result.confidence == 0.5
    assert result.candidate_supplier_name == "Old"
    assert result.matched_via is None
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_inactive_supplier_name_not_matched(db_session, seeded_suppliers):
    """The inactive supplier's name must not produce a match either."""
    result = await match_supplier(tokenize("Old Hardware"), db_session)

    # ``Old`` + ``Hardware`` are both capitalised + unmatched → two
    # candidates → no single candidate wins → conf 0.0.
    assert result.supplier_id is None
    assert result.confidence == 0.0
    assert result.candidate_supplier_name is None


@pytest.mark.asyncio
async def test_candidate_proposal_single(db_session, seeded_suppliers):
    """One capitalised unknown word → candidate proposal at conf 0.5.

    Original case is preserved.
    """
    result = await match_supplier(tokenize("Acme"), db_session)

    assert result.supplier_id is None
    assert result.confidence == 0.5
    assert result.candidate_supplier_name == "Acme"
    assert result.matched_via is None


@pytest.mark.asyncio
async def test_candidate_proposal_preserves_case(db_session, seeded_suppliers):
    """Mixed-case candidate keeps its original spelling (not normalised)."""
    result = await match_supplier(tokenize("AcmeCo"), db_session)

    assert result.confidence == 0.5
    assert result.candidate_supplier_name == "AcmeCo"


@pytest.mark.asyncio
async def test_candidate_proposal_cjk(db_session, seeded_suppliers):
    """CJK word with no DB match qualifies as a candidate (non-ASCII)."""
    # ``奇点`` is not a seeded supplier or alias, but is non-ASCII so
    # it counts as proper-noun-like → single candidate → conf 0.5.
    result = await match_supplier(tokenize("奇点"), db_session)

    assert result.confidence == 0.5
    assert result.candidate_supplier_name == "奇点"


@pytest.mark.asyncio
async def test_multiple_candidates_collapse_to_none(db_session, seeded_suppliers):
    """Two capitalised unknown words → ambiguous candidates → conf 0.0."""
    result = await match_supplier(tokenize("Acme Brands"), db_session)

    assert result.supplier_id is None
    assert result.confidence == 0.0
    assert result.candidate_supplier_name is None
    assert result.matched_via is None
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_no_match_lowercase_only(db_session, seeded_suppliers):
    """Lowercase-only unknown words never propose a candidate."""
    result = await match_supplier(tokenize("timber framing"), db_session)

    assert result == SupplierMatch(
        supplier_id=None,
        confidence=0.0,
        ambiguous_matches=(),
        candidate_supplier_name=None,
        matched_via=None,
    )


@pytest.mark.asyncio
async def test_match_plus_capitalised_noise(db_session, seeded_suppliers):
    """When a supplier matches, capitalised noise words are ignored.

    ``Mitre`` is an alias → unique match for sup_b. The extra
    capitalised word ``Delivery`` is not a candidate because we
    already have a DB match with conf 0.95.
    """
    _, sup_b, *_ = seeded_suppliers
    result = await match_supplier(tokenize("Mitre Delivery"), db_session)

    assert result.supplier_id == sup_b.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"
    assert result.candidate_supplier_name is None


# ---------------------------------------------------------------------------
# Normalisation + skipping behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_currency_and_numeric_tokens_skipped(db_session, seeded_suppliers):
    """Pure currency + numeric inputs never match or propose candidates."""
    result = await match_supplier(tokenize("$305 163"), db_session)

    assert result == SupplierMatch(
        supplier_id=None,
        confidence=0.0,
        ambiguous_matches=(),
        candidate_supplier_name=None,
        matched_via=None,
    )


@pytest.mark.asyncio
async def test_case_and_punctuation_insensitive(db_session, seeded_suppliers):
    """Upper-case + trailing punctuation still resolve via normalisation."""
    _, sup_b, *_ = seeded_suppliers
    # ``M-10!`` → normalises to ``m10`` → matches sup_b's M10 alias.
    result = await match_supplier(tokenize("M-10!"), db_session)

    assert result.supplier_id == sup_b.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_full_width_digits_via_alias(db_session, seeded_suppliers):
    """Full-width digits NFKC-fold into the half-width alias key."""
    _, sup_b, *_ = seeded_suppliers
    # U+FF2D FULLWIDTH CAPITAL M + U+FF11 FULLWIDTH DIGIT ONE +
    # U+FF10 FULLWIDTH DIGIT ZERO — normalize_alias NFKC-folds to
    # ``m10``.
    result = await match_supplier(tokenize("\uff2d\uff11\uff10"), db_session)

    assert result.supplier_id == sup_b.supplier_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_empty_input_returns_no_match(db_session, seeded_suppliers):
    """Empty token list short-circuits to the no-match result."""
    result = await match_supplier([], db_session)

    assert result == SupplierMatch(
        supplier_id=None,
        confidence=0.0,
        ambiguous_matches=(),
        candidate_supplier_name=None,
        matched_via=None,
    )


# ---------------------------------------------------------------------------
# Purity + data-class frozen contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pure_function_contract(db_session, seeded_suppliers):
    """``match_supplier`` must not mutate its input token list."""
    tokens = tokenize("$305 Bunnings Mitre bluemetal")
    before = copy.deepcopy(tokens)

    await match_supplier(tokens, db_session)

    assert tokens == before
    assert len(tokens) == len(before)
    for got, expected in zip(tokens, before, strict=True):
        assert got == expected


@pytest.mark.asyncio
async def test_supplier_match_is_frozen():
    """:class:`SupplierMatch` is frozen — catches accidental mutation."""
    from dataclasses import FrozenInstanceError

    sm = SupplierMatch(supplier_id=None, confidence=0.0)
    with pytest.raises(FrozenInstanceError):
        sm.confidence = 1.0  # type: ignore[misc]
