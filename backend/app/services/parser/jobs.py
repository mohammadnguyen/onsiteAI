"""Phase 2 Task T-G: job matcher for the expense-string parser.

Looks up the canonical :class:`~app.models.job.Job` for a parsed
expense by running each non-currency-symbol token through four
routes against the DB (numeric tokens ARE included — real-world
job codes / aliases / names include bare digits like ``"001"``,
``"003"``, ``"1"``):

1. :class:`~app.models.job.JobAlias` (normalised alias lookup — the
   primary / intended route)
2. :class:`~app.models.job.Job` ``job_code`` (exact normalised match)
3. :class:`~app.models.job.Job` ``job_name`` (exact normalised match
   against a single token)
4. :class:`~app.models.job.Job` ``job_name`` (Capture Hardening Patch
   CHP-1: exact normalised match against a CONTIGUOUS run of two or
   more word-ish tokens — lets multi-word names like ``"Smith
   Residence"`` match when the user types both words verbatim. The
   match is exact-equality on the concatenated normalised form, so
   short prefixes like ``"smith"`` alone never match — only the full
   name does.)

Only ``JobStatus.active`` jobs are ever returned. A completed job's
aliases are ignored for parser purposes (Phase 2 spec).

Contract (see :mod:`app.services.parser.llm_adapter` module docstring
for the full parser mutation contract):

1. :func:`match_job` is an **async** function (DB I/O is required) but
   otherwise obeys the stage-function contract: it consumes ``tokens``
   read-only and returns a narrow :class:`JobMatch`. It never
   constructs or touches a ``ParsePartial``; the orchestrator (T-K)
   does that.
2. The matcher does not mutate ``tokens`` (they are frozen) and does
   not reorder the list. The ``AsyncSession`` is used only for reads
   (``SELECT``) — no flush, no commit, no add.
3. Ambiguity — two or more distinct jobs matched across the token
   stream / routes — is returned as ``confidence=0.3`` with the sorted
   UUID tuple in ``ambiguous_matches``. The orchestrator / review
   queue decides how to render this.
4. ``matched_via`` reflects the priority order
   ``alias > code > name > multi_token_name`` for unique matches.
   It is ``None`` for the ambiguous and no-match cases so callers
   can't accidentally misreport a route.

Note on confidence (CHP-1)
--------------------------
All four routes return :data:`_CONF_UNIQUE` (``0.95``) on a unique
match. The multi-token-name route is treated as equally certain as the
single-token-name route because both are exact-equality matches against
``normalize_alias(job_name)`` — the user typed the full job name and
there's no fuzzy step. Per the Capture Hardening Patch behaviour table,
**no expense is saved with ``job_uncertain``**: anything less certain
than an exact match (e.g. a unique single-token shorthand prefix) is
not handled here and the service returns HTTP 422 instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_alias
from app.models import Job, JobAlias, JobStatus
from app.services.parser.tokens import Token

# Confidence tiers — named here so the tests pin them, not just magic
# numbers scattered through the function.
_CONF_UNIQUE = 0.95
_CONF_AMBIGUOUS = 0.3
_CONF_NONE = 0.0


@dataclass(frozen=True)
class JobMatch:
    """Result of the job-matching stage.

    - ``job_id``: the matched job's UUID, or ``None`` if no confident
      match.
    - ``confidence``: ``0.95`` for a single unique match across all
      tokens + routes, ``0.3`` for ambiguous (2+ matches), ``0.0`` for
      no match.
    - ``ambiguous_matches``: sorted tuple of UUIDs when 2+ jobs
      matched. Empty tuple otherwise. Given to the orchestrator for
      diagnostics and review-queue rendering.
    - ``matched_via``: one of ``'alias'``, ``'code'``, ``'name'``, or
      ``None`` — which lookup route produced the unique match.
      Priority order ``alias > code > name``. ``None`` for the
      ambiguous and no-match cases.
    """

    job_id: uuid.UUID | None
    confidence: float
    ambiguous_matches: tuple[uuid.UUID, ...] = ()
    matched_via: str | None = None


def _word_normals(
    tokens: list[Token],
    excluded_span: tuple[int, int] | None = None,
) -> list[str]:
    """Extract the normalised form of every lookup-candidate token.

    Currency-symbol tokens (``$ ¥ € £ ₩ ₹``) are skipped — no job is
    named with a bare currency glyph. Numeric tokens ARE kept (they
    can legitimately be a job code, alias, or name in real data —
    e.g. code ``"001"``, alias ``"003"``, name ``"1"``). Empty
    normalised strings (defensive) are also filtered.

    Amount-source exclusion (operator guardrail against silent
    financial mis-allocation): if ``excluded_span`` is supplied (the
    ``source_span`` of the winning amount token from the amount
    stage), any token whose ``span`` matches is dropped from the
    candidate set. This prevents a token that was consumed as the
    amount from being matched as a job — without this, a bare
    numeric amount like "100" could silently assign the expense to
    a job whose code happens to be "100".

    Returns a list so the caller can both use it as a set (for the
    alias IN-clause) and as an ordered stream (for route-priority
    tie-breaks on the first hit).
    """
    out: list[str] = []
    for tok in tokens:
        if tok.is_currency_symbol:
            continue
        if excluded_span is not None and tok.span == excluded_span:
            continue
        if not tok.normalized:
            continue
        out.append(tok.normalized)
    return out


def _multi_token_normals(normals: list[str]) -> set[str]:
    """Build the set of all contiguous-N-token concatenations (N>=2).

    For a token stream ``["smith", "residence", "bunnings", "cement"]``
    returns ``{"smithresidence", "residencebunnings", "bunningscement",
    "smithresidencebunnings", "residencebunningscement",
    "smithresidencebunningscement"}`` — every contiguous span of two or
    more word-ish tokens, joined into a single normalised string.

    Single tokens are NOT included here — the existing single-token
    name route handles those separately at higher priority.

    The resulting set is what we look up against
    ``normalize_alias(job_name)`` for the multi-token name route.
    Capped at a maximum span length so a runaway long input string
    can't blow up: in practice job names rarely exceed 6 words, and
    longer spans would be byte-noise anyway.
    """
    _MAX_SPAN = 8  # cap N-gram length; job names beyond this are unrealistic
    n = len(normals)
    out: set[str] = set()
    for start in range(n):
        # span of length L starting at `start`, where L >= 2.
        max_len = min(_MAX_SPAN, n - start)
        if max_len < 2:
            continue
        # Build progressively: smith → smithresidence → smithresidencebunnings...
        accum = normals[start]
        for length in range(2, max_len + 1):
            accum = accum + normals[start + length - 1]
            out.add(accum)
    return out


async def match_job(
    tokens: list[Token],
    db: AsyncSession,
    excluded_span: tuple[int, int] | None = None,
) -> JobMatch:
    """Match the token stream against active jobs.

    Pure w.r.t. inputs: ``tokens`` is consumed read-only and the
    ``AsyncSession`` is used only for ``SELECT``. Returns a
    :class:`JobMatch`; never constructs a ``ParsePartial``.

    ``excluded_span``: optional ``(start, end)`` offsets of the token
    consumed by the amount stage. When supplied, that token is
    dropped from the lookup-candidate set so it can't silently
    become a job match. Wired through by the orchestrator from
    :attr:`AmountMatch.source_span`. See ``_word_normals`` for the
    operator guardrail rationale.

    Strategy (two queries, Phase 2 simplicity):

    1. One ``SELECT JobAlias`` filtered by
       ``alias_text_normalized IN (<token normals>)`` with the parent
       :class:`Job` eager-loaded; reject rows whose parent job is
       ``completed``.
    2. One ``SELECT Job`` filtered by ``status == active`` — iterate
       Python-side and compare ``normalize_alias(job_code)`` /
       ``normalize_alias(job_name)`` to each token normal.

    Across both queries and all tokens, collect the set of unique
    matching ``job_id`` values and bucket the route (alias / code /
    name) that each match came through so we can report
    ``matched_via`` with the documented priority.
    """
    normals = _word_normals(tokens, excluded_span=excluded_span)
    if not normals:
        return JobMatch(
            job_id=None,
            confidence=_CONF_NONE,
            ambiguous_matches=(),
            matched_via=None,
        )

    normals_set = set(normals)
    # CHP-1: contiguous N-token concatenations (N>=2) for multi-word
    # job names. Built once here, looked up per-job in the same scan
    # loop as the single-token name match.
    multi_token_normals = _multi_token_normals(normals)

    # Track which routes matched which jobs; priority
    # alias > code > name > multi_token_name.
    by_route: dict[str, set[uuid.UUID]] = {
        "alias": set(),
        "code": set(),
        "name": set(),
        "multi_token_name": set(),
    }

    # --- Route 1: alias lookup ---
    alias_stmt = (
        select(JobAlias)
        .where(JobAlias.alias_text_normalized.in_(normals_set))
        .options(selectinload(JobAlias.job))
    )
    alias_rows = (await db.execute(alias_stmt)).scalars().all()
    for alias in alias_rows:
        if alias.job is not None and alias.job.status == JobStatus.active:
            by_route["alias"].add(alias.job_id)

    # --- Routes 2 + 3 + 4: scan active jobs for code / single-token-name
    # / multi-token-name hits.
    jobs_stmt = select(Job).where(Job.status == JobStatus.active)
    active_jobs = (await db.execute(jobs_stmt)).scalars().all()
    for job in active_jobs:
        if job.job_code is not None:
            code_normal = normalize_alias(job.job_code)
            if code_normal and code_normal in normals_set:
                by_route["code"].add(job.job_id)
        name_normal = normalize_alias(job.job_name)
        if name_normal:
            if name_normal in normals_set:
                # Single-token exact name match (existing behaviour).
                by_route["name"].add(job.job_id)
            elif name_normal in multi_token_normals:
                # CHP-1 multi-token contiguous name match. Same exact-
                # equality test, just against the joined N-gram set.
                # This route ONLY fires if the single-token route did
                # not already match (the single-token name normal is
                # never in the multi-token set by construction, since
                # multi_token_normals only contains spans of N>=2).
                by_route["multi_token_name"].add(job.job_id)

    all_matches = (
        by_route["alias"]
        | by_route["code"]
        | by_route["name"]
        | by_route["multi_token_name"]
    )

    if not all_matches:
        return JobMatch(
            job_id=None,
            confidence=_CONF_NONE,
            ambiguous_matches=(),
            matched_via=None,
        )

    if len(all_matches) > 1:
        return JobMatch(
            job_id=None,
            confidence=_CONF_AMBIGUOUS,
            ambiguous_matches=tuple(sorted(all_matches)),
            matched_via=None,
        )

    # Exactly one unique job across every route. Assign ``matched_via``
    # with the documented priority:
    # alias > code > name > multi_token_name.
    (unique_id,) = all_matches
    for route in ("alias", "code", "name", "multi_token_name"):
        if unique_id in by_route[route]:
            return JobMatch(
                job_id=unique_id,
                confidence=_CONF_UNIQUE,
                ambiguous_matches=(),
                matched_via=route,
            )

    # Unreachable by construction (``all_matches`` is the union) but
    # keep the fallback explicit so a future refactor can't silently
    # return a ``matched_via=None`` on a unique hit.
    return JobMatch(
        job_id=unique_id,
        confidence=_CONF_UNIQUE,
        ambiguous_matches=(),
        matched_via=None,
    )
