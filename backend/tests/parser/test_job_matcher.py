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
async def test_currency_and_numeric_tokens_skipped(db_session, seeded_jobs):
    """Pure currency + numeric inputs never match a job."""
    result = await match_job(tokenize("$305 163"), db_session)

    assert result.job_id is None
    assert result.confidence == 0.0
    assert result.ambiguous_matches == ()
    assert result.matched_via is None


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
