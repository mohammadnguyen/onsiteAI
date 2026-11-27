"""What the reviewer is told.

This text is part of the workflow's behaviour, not decoration: an earlier
version asked for "a minimal in-scope fix" and told the reviewer not to
propose work outside the allowed scope, which selects for the smallest
change rather than the right one and instructs the reviewer to keep quiet
about necessary work. Both are corrected here.

The standard this repository judges a solution by is correctness,
maintainability and fit with the existing project — never "smallest diff".
And a necessary change that falls outside the approved scope must be
reported, loudly, with its own marker: the run then stops and asks the
founder for authorisation. Nobody, human or model, is asked to hide it.
"""

from __future__ import annotations

OUT_OF_SCOPE_MARKER = "OUT-OF-SCOPE"


def review_instructions(
    *,
    package: str,
    base: str,
    requirements: list[str],
    acceptance_criteria: list[str],
    allowed_paths: list[str],
    prohibitions: list[str],
    gate_logs: list[str],
    run_dir: str,
    extra: str = "",
) -> str:
    """The focus text handed to the reviewer as a single argument."""
    evidence = ", ".join(gate_logs) or run_dir
    parts = [
        f"Work package: {package}.",
        f"Review the diff {base}..HEAD plus any uncommitted changes in the "
        "working tree; that is the whole change under review.",
        "Approved requirements: " + " | ".join(requirements),
        "Acceptance criteria: " + " | ".join(acceptance_criteria),
        "Approved change scope: " + " | ".join(allowed_paths),
        "Prohibited: " + " | ".join(prohibitions),
        "The verification commands in the brief were run against this exact "
        f"head; their raw output is archived at: {evidence}. Read it rather "
        "than trusting any summary of it.",
        # The judging standard, stated positively and in this order.
        "Judge the change on three things, in this order: CORRECTNESS (does "
        "it do what the requirements say, including on failure paths, "
        "concurrency, and partial failure); MAINTAINABILITY (can the next "
        "person change it safely - naming, structure, tests that would catch "
        "a regression, comments that explain why); and PROJECT FIT (does it "
        "follow the conventions and architecture already in this repository "
        "rather than importing a foreign pattern). Do not prefer a smaller "
        "change to a correct one, and do not withhold a finding because the "
        "fix would be large.",
        "Report only grounded defects: each needs file:line, a concrete "
        "scenario in which it goes wrong, and what you would do about it.",
        # Item 3: out-of-scope work is reported, never concealed, never
        # silently made.
        "If a change you consider NECESSARY falls outside the approved scope "
        f"above, report it anyway and prefix its title with {OUT_OF_SCOPE_MARKER}. "
        "Say why it is necessary. Do not suppress it, do not soften it into a "
        "suggestion, and do not treat the scope list as a reason to stay "
        "silent. The run will stop and ask the founder for authorisation; "
        "nothing outside the approved scope is changed automatically.",
        "End with a single line 'Verdict: approve' or 'Verdict: "
        "needs-attention'. Approve only if you found no defect that should "
        "block this package; if you are unsure, say needs-attention.",
    ]
    if extra:
        parts.append(extra)
    return " ".join(parts)
