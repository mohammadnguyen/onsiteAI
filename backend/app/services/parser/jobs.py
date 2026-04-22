"""Phase 2 Task T-G: job matcher for the expense-string parser.

Looks up the canonical :class:`~app.models.job.Job` for a parsed
expense by running each non-currency / non-numeric token through three
routes against the DB:

1. :class:`~app.models.job.JobAlias` (normalised alias lookup — the
   primary / intended route)
2. :class:`~app.models.job.Job` ``job_code`` (exact normalised match)
3. :class:`~app.models.job.Job` ``job_name`` (exact normalised match)

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
4. ``matched_via`` reflects the priority order ``alias > code > name``
   for unique matches. It is ``None`` for the ambiguous and no-match
   cases so callers can't accidentally misreport a route.
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


def _word_normals(tokens: list[Token]) -> list[str]:
    """Extract the normalised form of every word-ish token.

    Currency symbols and numeric-like tokens are skipped — jobs are
    never named with bare digits or ``$``. Empty normalised strings
    (defensive) are also filtered. Returns a list so the caller can
    both use it as a set (for the alias IN-clause) and as an ordered
    stream (for route-priority tie-breaks on the first hit).
    """
    out: list[str] = []
    for tok in tokens:
        if tok.is_currency_symbol or tok.is_numeric_like:
            continue
        if not tok.normalized:
            continue
        out.append(tok.normalized)
    return out


async def match_job(tokens: list[Token], db: AsyncSession) -> JobMatch:
    """Match the token stream against active jobs.

    Pure w.r.t. inputs: ``tokens`` is consumed read-only and the
    ``AsyncSession`` is used only for ``SELECT``. Returns a
    :class:`JobMatch`; never constructs a ``ParsePartial``.

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
    normals = _word_normals(tokens)
    if not normals:
        return JobMatch(
            job_id=None,
            confidence=_CONF_NONE,
            ambiguous_matches=(),
            matched_via=None,
        )

    normals_set = set(normals)

    # Track which routes matched which jobs; priority alias > code > name.
    by_route: dict[str, set[uuid.UUID]] = {"alias": set(), "code": set(), "name": set()}

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

    # --- Routes 2 + 3: scan active jobs for code / name hits ---
    jobs_stmt = select(Job).where(Job.status == JobStatus.active)
    active_jobs = (await db.execute(jobs_stmt)).scalars().all()
    for job in active_jobs:
        if job.job_code is not None:
            code_normal = normalize_alias(job.job_code)
            if code_normal and code_normal in normals_set:
                by_route["code"].add(job.job_id)
        name_normal = normalize_alias(job.job_name)
        if name_normal and name_normal in normals_set:
            by_route["name"].add(job.job_id)

    all_matches = by_route["alias"] | by_route["code"] | by_route["name"]

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
    # with the documented priority: alias first, then code, then name.
    (unique_id,) = all_matches
    for route in ("alias", "code", "name"):
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
