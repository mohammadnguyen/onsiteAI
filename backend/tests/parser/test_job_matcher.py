"""Phase 2 Task T-G: DB-backed tests for the job matcher.

Exercises :func:`app.services.parser.jobs.match_job` against real
Postgres (5433). Every test seeds a small job graph — two active jobs
with English + Chinese aliases and one completed job — then feeds
``tokenize`` output through the matcher and asserts on the narrow
:class:`JobMatch` result.

Tests cover:

* single alias (EN + zh) matches
* alias buried inside a phrase with currency + numeric noise
* exact ``job_code`` and ``job_name`` routes
* ambiguity when two tokens hit different jobs
* ``status=completed`` jobs are never returned
* currency + numeric tokens are skipped (no accidental matches)
* case + punctuation insensitivity via :func:`normalize_alias`
* NFKC folding (full-width digits → half-width aliases)
* purity — the matcher does not mutate its input token list
"""

from __future__ import annotations

import copy
import uuid

import pytest
import pytest_asyncio

from app.models import Job, JobAlias, JobStatus, LanguageCode
from app.services.parser.jobs import JobMatch, match_job
from app.services.parser.tokens import tokenize


async def _make_job(
    db_session,
    admin,
    *,
    name: str,
    code: str | None = None,
    status: JobStatus = JobStatus.active,
) -> Job:
    """Insert a bare :class:`Job` into the current transaction."""
    job = Job(
        job_id=uuid.uuid4(),
        job_code=code,
        job_name=name,
        status=status,
        created_by=admin.user_id,
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest_asyncio.fixture
async def seeded_jobs(db_session, seeded_admin):
    """Seed two active jobs + one completed job, each with aliases.

    * Job A — ``Kelly House`` (code ``KH-01``), aliases ``Kelly``,
      ``工地1``.
    * Job B — ``Smith Reno`` (code ``SR-02``), aliases ``Smith``,
      ``Smith Site``.
    * Job C — ``Old Project`` (code ``OLD-09``, ``completed``),
      alias ``Old``.

    A fourth active job ``RenoSite Pty`` (no aliases) is seeded so the
    name-route test has a distinctive token that is NOT also an alias.

    Returned as a 4-tuple ``(job_a, job_b, job_c, job_d)`` so tests can
    index by position without dict lookups.
    """
    job_a = await _make_job(db_session, seeded_admin, name="Kelly House", code="KH-01")
    db_session.add_all(
        [
            JobAlias(
                job_id=job_a.job_id,
                alias_text="Kelly",
                language_code=LanguageCode.en,
            ),
            JobAlias(
                job_id=job_a.job_id,
                alias_text="工地1",
                language_code=LanguageCode.zh,
            ),
        ]
    )

    job_b = await _make_job(db_session, seeded_admin, name="Smith Reno", code="SR-02")
    db_session.add_all(
        [
            JobAlias(
                job_id=job_b.job_id,
                alias_text="Smith",
                language_code=LanguageCode.en,
            ),
            JobAlias(
                job_id=job_b.job_id,
                alias_text="Smith Site",
                language_code=LanguageCode.en,
            ),
        ]
    )

    job_c = await _make_job(
        db_session,
        seeded_admin,
        name="Old Project",
        code="OLD-09",
        status=JobStatus.completed,
    )
    db_session.add(
        JobAlias(
            job_id=job_c.job_id,
            alias_text="Old",
            language_code=LanguageCode.en,
        )
    )

    # Active job with a distinctive single-token name that no alias
    # mentions — lets us test the pure ``matched_via='name'`` route
    # cleanly. A single-token name is required because the matcher
    # compares ``normalize_alias(job_name)`` against each token's
    # normal, and multi-word names normalise to a single concatenated
    # key that won't equal any individual token.
    job_d = await _make_job(db_session, seeded_admin, name="RenoSite")

    await db_session.flush()
    return (job_a, job_b, job_c, job_d)


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_alias_match(db_session, seeded_jobs):
    """Plain English alias → unique match via the alias route."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("Kelly"), db_session)

    assert result == JobMatch(
        job_id=job_a.job_id,
        confidence=0.95,
        ambiguous_matches=(),
        matched_via="alias",
    )


@pytest.mark.asyncio
async def test_chinese_alias_match(db_session, seeded_jobs):
    """CJK alias → unique match via the alias route."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("工地1"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_alias_mixed_in_phrase(db_session, seeded_jobs):
    """Alias buried in currency + numeric + supplier noise still matches."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("$305 Bunnings Kelly bluemetal"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_alias_chinese_in_phrase(db_session, seeded_jobs):
    """CJK alias buried in CJK + numeric noise still matches."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("工地1 水工材料 163"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_job_code_match(db_session, seeded_jobs):
    """``job_code`` (no alias row) routes via ``matched_via='code'``."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("KH-01"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "code"


@pytest.mark.asyncio
async def test_job_name_fallback(db_session, seeded_jobs):
    """``job_name`` (no alias, distinctive single-token name) → ``'name'``.

    Job D has a single-token ``job_name`` (``RenoSite``) and no
    aliases. A token whose normal equals ``normalize_alias(job_name)``
    must resolve via the ``name`` route only.
    """
    _, _, _, job_d = seeded_jobs
    result = await match_job(tokenize("RenoSite"), db_session)

    assert result.job_id == job_d.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "name"


# ---------------------------------------------------------------------------
# Ambiguity, no match, completed jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_two_jobs(db_session, seeded_jobs):
    """Two tokens matching two different jobs → ambiguous, conf 0.3."""
    job_a, job_b, *_ = seeded_jobs
    result = await match_job(tokenize("Kelly Smith"), db_session)

    assert result.job_id is None
    assert result.confidence == 0.3
    assert result.matched_via is None
    # Sorted tuple of the two matching UUIDs.
    assert result.ambiguous_matches == tuple(sorted([job_a.job_id, job_b.job_id]))


@pytest.mark.asyncio
async def test_no_match_returns_none(db_session, seeded_jobs):
    """Words that don't match any job return the empty JobMatch."""
    result = await match_job(tokenize("Random Words Nothing"), db_session)

    assert result == JobMatch(
        job_id=None,
        confidence=0.0,
        ambiguous_matches=(),
        matched_via=None,
    )


@pytest.mark.asyncio
async def test_completed_job_not_matched(db_session, seeded_jobs):
    """An alias on a ``completed`` job must not produce a match."""
    # "Old" is an alias on job_c (status=completed). Match must skip.
    result = await match_job(tokenize("Old"), db_session)

    assert result.job_id is None
    assert result.confidence == 0.0
    assert result.matched_via is None
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_completed_job_code_and_name_not_matched(db_session, seeded_jobs):
    """Completed job's code + name are both ignored by the matcher."""
    result_code = await match_job(tokenize("OLD-09"), db_session)
    result_name = await match_job(tokenize("Old Project"), db_session)

    assert result_code.job_id is None
    assert result_code.confidence == 0.0
    assert result_name.job_id is None
    assert result_name.confidence == 0.0


@pytest.mark.asyncio
async def test_unmatched_currency_and_numeric_tokens_yield_no_job(
    db_session, seeded_jobs
):
    """Currency + numeric tokens that don't match any seeded code /
    alias / name yield no match.

    Note: this is a weaker invariant than the pre-fix behaviour.
    Numeric tokens are no longer filtered out of the candidate set
    (real data uses numeric codes / aliases / names — e.g. ``"001"``,
    ``"003"``, ``"1"``), so a numeric token CAN match a job if a job
    is configured with that exact value. The ``seeded_jobs`` fixture
    uses alphanumeric codes (``KH-01``, ``SR-02``, ``OLD-09``) so
    ``305`` and ``163`` happen not to match — see the dedicated
    digit-only positive tests below.
    """
    result = await match_job(tokenize("$305 163"), db_session)

    assert result.job_id is None
    assert result.confidence == 0.0
    assert result.ambiguous_matches == ()
    assert result.matched_via is None


# ---------------------------------------------------------------------------
# Digit-only code / alias / name routes (operator-reported gap)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digit_only_job_code_matches(db_session, seeded_admin):
    """A digit-only ``job_code`` like ``"001"`` matches via the code route.

    Regression: pre-fix the matcher filtered numeric tokens before
    DB lookup, so ``"001 plumbing $100"`` could never match the
    ``晶晶家`` job whose code was ``001``. This test pins the
    post-fix behaviour.
    """
    job = await _make_job(db_session, seeded_admin, name="晶晶家", code="001")

    result = await match_job(tokenize("001 plumbing $100"), db_session)

    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "code"


@pytest.mark.asyncio
async def test_digit_only_alias_matches(db_session, seeded_admin):
    """A digit-only ``JobAlias.alias_text`` like ``"003"`` matches via the alias route."""
    job = await _make_job(db_session, seeded_admin, name="31API")
    db_session.add(
        JobAlias(
            job_id=job.job_id,
            alias_text="003",
            language_code=LanguageCode.en,
        )
    )
    await db_session.flush()

    result = await match_job(tokenize("003 cement $50"), db_session)

    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_digit_only_job_name_matches(db_session, seeded_admin):
    """A digit-only ``job_name`` like ``"1"`` matches via the name route."""
    job = await _make_job(db_session, seeded_admin, name="1")

    result = await match_job(tokenize("1 plumbing $250"), db_session)

    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "name"


# ---------------------------------------------------------------------------
# Amount-token exclusion guardrail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amount_source_token_excluded_from_job_match(
    db_session, seeded_admin
):
    """A token consumed as the amount must not silently match a job.

    Operator guardrail against silent financial mis-allocation. If a
    job is configured with code ``"100"`` and the user captures
    ``"plumbing 100"``, the bare ``100`` is interpreted as the
    amount; passing the amount's ``source_span`` as
    ``excluded_span`` to ``match_job`` MUST drop that token from the
    job candidate set so the expense is NOT silently assigned to
    the job with code ``"100"``.
    """
    from app.services.parser.amount import extract_amount

    await _make_job(db_session, seeded_admin, name="JobOneHundred", code="100")

    tokens = tokenize("plumbing 100")
    amt = extract_amount(tokens)
    # Sanity: the amount stage consumed the "100" token.
    assert amt.value is not None
    assert amt.source_span is not None

    result = await match_job(tokens, db_session, excluded_span=amt.source_span)

    # The amount token was excluded; no other token matches code/alias/name.
    assert result.job_id is None
    assert result.confidence == 0.0
    assert result.matched_via is None


@pytest.mark.asyncio
async def test_excluded_span_does_not_block_unrelated_numeric_token(
    db_session, seeded_admin
):
    """Excluding the amount token must NOT block other numeric tokens
    from matching jobs.

    Captures like ``"001 plumbing $100"`` need the ``001`` token to
    match the job code while ``$100`` is independently consumed as
    the amount. The amount's ``source_span`` covers the ``100``
    portion of ``$100``; the standalone ``001`` token has a
    different span and must still flow through to the job matcher.
    """
    from app.services.parser.amount import extract_amount

    job = await _make_job(db_session, seeded_admin, name="晶晶家", code="001")

    tokens = tokenize("001 plumbing $100")
    amt = extract_amount(tokens)
    assert amt.value is not None
    assert amt.source_span is not None

    result = await match_job(tokens, db_session, excluded_span=amt.source_span)

    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "code"


# ---------------------------------------------------------------------------
# Normalisation behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_and_punctuation_insensitive(db_session, seeded_jobs):
    """Upper-case + trailing punctuation still resolve via normalization."""
    job_a, *_ = seeded_jobs
    result = await match_job(tokenize("KELLY!"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_full_width_digits_via_alias(db_session, seeded_jobs):
    """Full-width digit ``１`` NFKC-folds into the half-width alias key."""
    job_a, *_ = seeded_jobs
    # U+FF11 FULLWIDTH DIGIT ONE — normalize_alias NFKC-folds to "1".
    result = await match_job(tokenize("工地\uff11"), db_session)

    assert result.job_id == job_a.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_alias_route_beats_name_route_on_tie(db_session, seeded_jobs):
    """If the same job matches via both alias and name, ``matched_via``
    reports the higher-priority route (alias)."""
    # Smith is an alias on job_b; "Smith Reno" is also the job_name.
    # Normalised tokens will be ``smith`` and ``reno``. ``smith``
    # matches job_b via alias; the name normal is ``smithreno`` which
    # doesn't match a single token. So this tests the alias route hits
    # cleanly even when name-route scanning is performed. Add a second
    # check via the job_code to lock in the priority order.
    job_b = seeded_jobs[1]
    # ``smith`` matches alias; ``sr02`` (normalised ``SR-02``) matches
    # code — the same job_b via TWO routes. matched_via must be
    # ``'alias'`` (higher priority).
    result = await match_job(tokenize("Smith SR-02"), db_session)

    assert result.job_id == job_b.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


# ---------------------------------------------------------------------------
# Purity contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pure_function_contract(db_session, seeded_jobs):
    """``match_job`` must not mutate its input token list."""
    tokens = tokenize("$305 Bunnings Kelly bluemetal")
    before = copy.deepcopy(tokens)

    await match_job(tokens, db_session)

    assert tokens == before
    # Also check list identity + length — no reordering, no append/pop.
    assert len(tokens) == len(before)
    for got, expected in zip(tokens, before, strict=True):
        assert got is tokens[tokens.index(got)]
        assert got == expected


@pytest.mark.asyncio
async def test_empty_input_returns_no_match(db_session, seeded_jobs):
    """Empty token list short-circuits to the no-match result."""
    result = await match_job([], db_session)

    assert result == JobMatch(
        job_id=None,
        confidence=0.0,
        ambiguous_matches=(),
        matched_via=None,
    )


@pytest.mark.asyncio
async def test_job_match_is_frozen():
    """:class:`JobMatch` is frozen — catches accidental mutation."""
    from dataclasses import FrozenInstanceError

    jm = JobMatch(job_id=None, confidence=0.0)
    with pytest.raises(FrozenInstanceError):
        jm.confidence = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CHP-1: multi-token contiguous job-name match
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chp1_jobs(db_session, seeded_admin):
    """Seed two multi-word English jobs + a multi-word CJK job for CHP-1 tests.

    These names are deliberately multi-token so the existing single-token
    name route doesn't match — only the new multi-token contiguous route
    can resolve them.

    Returned tuple: ``(smith_residence, brown_renovation, jingjing_jia)``.
    """
    smith = await _make_job(
        db_session, seeded_admin, name="Smith Residence", code="SMITH-01"
    )
    brown = await _make_job(
        db_session, seeded_admin, name="Brown Renovation", code="BROWN-03"
    )
    # Multi-token CJK name — exercises the route's NFKC + multi-token path
    # for non-ASCII characters too.
    jingjing = await _make_job(
        db_session, seeded_admin, name="晶晶 家", code="JJ-02"
    )
    await db_session.flush()
    return (smith, brown, jingjing)


@pytest.mark.asyncio
async def test_chp1_full_multi_word_name_resolves(db_session, chp1_jobs):
    """Case A: ``"Smith Residence Bunnings $440 cement"`` resolves to Smith.

    Multi-token contiguous concatenation ``smith`` + ``residence`` =
    ``smithresidence`` exactly equals ``normalize_alias("Smith Residence")``.
    Confidence is 0.95 (treated as exact-equality, not a fuzzy guess).
    """
    smith, _, _ = chp1_jobs
    result = await match_job(
        tokenize("Smith Residence Bunnings $440 cement"), db_session
    )
    assert result.job_id == smith.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "multi_token_name"
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_chp1_full_multi_word_lowercase_resolves(db_session, chp1_jobs):
    """Case A lowercase: case-folding via ``normalize_alias`` still resolves."""
    smith, _, _ = chp1_jobs
    result = await match_job(tokenize("smith residence 100 concrete"), db_session)
    assert result.job_id == smith.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "multi_token_name"


@pytest.mark.asyncio
async def test_chp1_full_multi_word_punctuation_safe(db_session, chp1_jobs):
    """Case A punctuation-safe: tokenizer splits on whitespace, ``normalize_alias``
    strips punctuation. ``"Brown-Renovation $5400 stratco"`` works because the
    tokenizer keeps ``"Brown-Renovation"`` as one token, normalised to
    ``"brownrenovation"`` — which equals ``normalize_alias("Brown Renovation")``.
    """
    _, brown, _ = chp1_jobs
    result = await match_job(tokenize("Brown-Renovation $5400 stratco"), db_session)
    assert result.job_id == brown.job_id
    assert result.confidence == 0.95
    # Single-token route wins here because ``"Brown-Renovation"`` is one token
    # whose normalised form already equals the job-name normal — the
    # multi-token route is only consulted when no single token equals the name.
    assert result.matched_via == "name"


@pytest.mark.asyncio
async def test_chp1_full_multi_word_cjk_resolves(db_session, chp1_jobs):
    """Case A CJK: ``"晶晶 家 水泥 800"`` matches the multi-token CJK job name."""
    _, _, jingjing = chp1_jobs
    result = await match_job(tokenize("晶晶 家 水泥 800"), db_session)
    assert result.job_id == jingjing.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "multi_token_name"


@pytest.mark.asyncio
async def test_chp1_unique_shorthand_does_not_save(db_session, chp1_jobs):
    """Case B: ``"smith"`` alone does NOT match — the parser returns no
    match, so the SERVICE LAYER is responsible for the "Did you mean"
    suggestion (covered by API-level tests). The matcher itself stays
    strict: only an exact alias / code / single-token name / multi-token
    contiguous concatenation match returns a job.

    This locks in the contract: per the Capture Hardening Patch behaviour
    table, a unique shorthand prefix must NOT save with ``job_uncertain``
    — admin cannot subsequently correct ``job_id``.
    """
    result = await match_job(tokenize("Bunnings $440 cement smith"), db_session)
    assert result.job_id is None
    assert result.confidence == 0.0
    assert result.matched_via is None
    assert result.ambiguous_matches == ()


@pytest.mark.asyncio
async def test_chp1_alias_promotes_shorthand_to_save(db_session, seeded_admin):
    """Case B-with-alias: if ``"smith"`` IS configured as a JobAlias, the
    alias route resolves at 0.95 (existing behaviour) and the expense
    saves cleanly.
    """
    smith = await _make_job(
        db_session, seeded_admin, name="Smith Residence", code="SMITH-01"
    )
    db_session.add(
        JobAlias(
            job_id=smith.job_id,
            alias_text="smith",
            language_code=LanguageCode.en,
        )
    )
    await db_session.flush()

    result = await match_job(tokenize("Bunnings $440 cement smith"), db_session)
    assert result.job_id == smith.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_chp1_ambiguous_two_codes_returns_both(db_session, chp1_jobs):
    """Case C-style at the matcher level: input mentioning two valid codes
    returns ``confidence=0.3`` with both UUIDs in ``ambiguous_matches``.
    The service layer turns this into the actionable 422 (CHP-2).
    """
    smith, brown, _ = chp1_jobs
    result = await match_job(
        tokenize("SMITH-01 BROWN-03 plumbing 1100"), db_session
    )
    assert result.job_id is None
    assert result.confidence == 0.3
    assert result.matched_via is None
    assert result.ambiguous_matches == tuple(sorted([smith.job_id, brown.job_id]))


@pytest.mark.asyncio
async def test_chp1_priority_alias_beats_multi_token_name(db_session, seeded_admin):
    """If the same job matches via BOTH the alias route AND the multi-token
    name route, ``matched_via`` reports the higher-priority route (alias).
    """
    job = await _make_job(
        db_session, seeded_admin, name="Smith Residence", code="SMITH-01"
    )
    # Add an alias on a single token from the input that ALSO appears in
    # the multi-token concatenation. Both routes will match the same job.
    db_session.add(
        JobAlias(
            job_id=job.job_id,
            alias_text="smith",
            language_code=LanguageCode.en,
        )
    )
    await db_session.flush()

    # "smith residence" matches:
    # - alias on "smith" → job
    # - multi-token "smithresidence" → job (same UUID)
    # Single unique match across all routes; matched_via must be alias.
    result = await match_job(tokenize("smith residence 100"), db_session)
    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "alias"


@pytest.mark.asyncio
async def test_chp1_priority_name_beats_multi_token_name(db_session, seeded_admin):
    """If a single-token name match AND a multi-token name match resolve to
    the same job, ``matched_via`` reports the higher-priority route (name).

    Concrete shape: a single-token job name ``"Renositesmith"`` (one token,
    no spaces) plus an input containing both ``"renositesmith"`` and the
    multi-token ``"smith residence"`` would be artificial. Use a simpler
    setup: a job named ``"Smith"`` (single token) — input ``"smith
    residence 100"`` matches via ``name`` (single token "smith" equals
    job_name "Smith"). The multi-token route also tries
    ``"smithresidence"`` against the same job_name "smith" — that does
    NOT equal "smithresidence", so the multi-token route does not match
    this job. The single-token name route wins cleanly.
    """
    job = await _make_job(db_session, seeded_admin, name="Smith")
    await db_session.flush()
    result = await match_job(tokenize("smith residence 100"), db_session)
    assert result.job_id == job.job_id
    assert result.confidence == 0.95
    assert result.matched_via == "name"


# ---------------------------------------------------------------------------
# Job Lifecycle v1A-2: archive transition is honored by parser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archived_job_excluded_from_parser_match(
    db_session, seeded_admin
):
    """Regression for v1A-2: when a previously-active job is archived
    via a status transition mid-session (active → completed), the
    parser must immediately stop returning it on subsequent calls.

    The seeded_jobs fixture already covers the static "completed job
    is ignored at seed time" case via test_completed_job_not_matched
    and test_completed_job_code_and_name_not_matched. This test adds
    the dynamic-transition angle that v1A-2's UI exposes: the user
    creates an active job, the parser matches it; the user archives
    via PATCH (which the admin web's new JobLifecycleActions button
    drives); the parser stops matching it on the next call. No
    caching to invalidate; the parser re-reads every call (see
    services/parser/jobs.py).
    """
    # 1. Create an active job with a distinctive single-token name +
    #    code so the matcher has clean routes to test.
    job = await _make_job(
        db_session,
        seeded_admin,
        name="ArchiveLifecycleJob",
        code="ALJ-01",
    )
    await db_session.flush()

    # 2. Confirm both routes match while active.
    by_name = await match_job(
        tokenize("ArchiveLifecycleJob 100"), db_session
    )
    assert by_name.job_id == job.job_id, "name route should match active job"
    assert by_name.matched_via == "name"

    by_code = await match_job(tokenize("ALJ-01 cement"), db_session)
    assert by_code.job_id == job.job_id, "code route should match active job"
    assert by_code.matched_via == "code"

    # 3. Archive the job (simulates the v1A-2 Archive button +
    #    backend PATCH /jobs/{id} {"status": "completed"}).
    job.status = JobStatus.completed
    await db_session.flush()

    # 4. Both routes must now return no match.
    by_name_after = await match_job(
        tokenize("ArchiveLifecycleJob 100"), db_session
    )
    assert by_name_after.job_id is None, (
        "archived job must not match by name"
    )
    assert by_name_after.confidence == 0.0
    assert by_name_after.matched_via is None

    by_code_after = await match_job(tokenize("ALJ-01 cement"), db_session)
    assert by_code_after.job_id is None, (
        "archived job must not match by code"
    )
    assert by_code_after.confidence == 0.0
    assert by_code_after.matched_via is None

    # 5. Reopen (simulates v1A-2 Reopen button) and confirm matches
    #    return — closes the loop on both transition directions.
    job.status = JobStatus.active
    await db_session.flush()

    by_name_reopened = await match_job(
        tokenize("ArchiveLifecycleJob 100"), db_session
    )
    assert by_name_reopened.job_id == job.job_id, (
        "reopened job should match again"
    )
    assert by_name_reopened.matched_via == "name"
