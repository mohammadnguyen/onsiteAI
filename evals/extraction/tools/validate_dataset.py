#!/usr/bin/env python3
"""Structural validator for extraction calibration/eval JSONL files.

Checks SHAPE only — never semantics. The current contract is
annotation-schema-v0.2: a corpus with a sidecar `<name>.manifest.json`
is validated as v0.2 (corpus provenance manifest, context.job_state,
optional fact_category, meta.modality, multi_job routing); a corpus
without one is validated as legacy v0.1 (warning) and contributes zero
cases to v0.2 Baseline readiness. Per-class counts are reported and
deliberately never combined into one total: reference, synthetic,
development and held-out captures are different kinds of evidence.

Two kinds of data, never conflated:

* **Public fixtures** (in-repo): the shipped sample file and an optional
  in-repo synthetic fixture. Synthetic/illustrative only; they never
  count toward the real-capture minimum.
* **Private datasets** (outside every registered Git worktree): real,
  held-out and reference material, located via --private-root or
  $FOREY_PRIVATE_CALIBRATION. Any in-repo path that is not a public
  fixture is refused, and the private root itself is refused if it sits
  inside any worktree (see path_policy.py — fails closed, exit 2).

Usage:
  python evals/extraction/tools/validate_dataset.py [FILES...]
      # no FILES: validates in-repo public fixtures plus every
      # private-root dataset file that exists
  python evals/extraction/tools/validate_dataset.py \
      --baseline-structure-ready --private-root D:/FOREY_PRIVATE_CALIBRATION
      # STRUCTURAL HELD-OUT gate: exit 0 iff the held-out corpus's v0.2
      # manifest satisfies the full eligibility quintuple (real,
      # contemporaneous_capture, verbatim true, ai_exposure none,
      # intended_use heldout) AND >= 30 structurally valid labelled
      # cases remain after excluding multi_job routing. Development,
      # legacy and AI-exposed data contribute zero. It cannot verify
      # independence, the elapsed week, disagreement resolution, or
      # freeze — Baseline v0.1 still requires explicit founder approval.

Exit codes: 0 = pass, 1 = structural violations, 2 = usage/policy error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from path_policy import (  # noqa: E402
    WorktreeDiscoveryError,
    find_violation,
    is_public_fixture,
    registered_worktrees,
)

EXTRACTION_DIR = Path(__file__).resolve().parent.parent

TYPES = {"site_log_fact", "task", "potential_variation"}
SUPPORT = {"explicit", "reasonable", "unknown", "ambiguous"}
LANGS = {"en", "zh", "mixed"}

# Minimum number of structurally valid labelled real cases. A floor, not
# a certificate: meeting it says nothing about how the labels were made.
MINIMUM_REAL_CASES = 30

# ---- schema v0.2 vocabulary (annotation-schema-v0.2.md) -------------------
# fact_category is metadata on an atomic site_log_fact (DEC-ONTOLOGY-001
# Amendment 10) — NOT a Candidate type. Generic 'issue' is deliberately
# absent (quality/safety/delivery/delay issues would overlap).
FACT_CATEGORIES = {
    "attendance", "progress", "site_condition", "quality", "delivery",
    "inspection", "safety", "delay", "incident", "instruction", "weather",
    "other",
}
JOB_STATES = {"confirmed", "unassigned"}
MODALITIES = {"text", "voice_transcript", "photo", "document", "other"}
ROUTING_CASES = {"multi_job"}

EVENT_ORIGINS = {"real", "synthetic", "unknown"}
CREATION_METHODS = {
    "contemporaneous_capture", "retrospective_reconstruction",
    "constructed_example",
}
# NOTE: verbatim_capture is validated type-strictly by
# valid_verbatim_capture() below, NOT by set membership — in Python,
# 0 == False and 1 == True, so `0 in {True, False, "unknown"}` would
# wrongly accept integers.
VERBATIM_DISPLAY = "true | false | \"unknown\""
AI_EXPOSURES = {"none", "raw_seen", "gold_seen"}
INTENDED_USES = {"reference", "development", "heldout"}

# Path–declaration agreement (schema v0.2 §2.1): the path is a storage/
# security constraint; the declaration is authoritative but must agree.
INTENDED_USE_BY_CLASS = {
    "sample": "reference",
    "synthetic": "reference",
    "reference": "reference",
    "real": "development",
    "heldout": "heldout",
}

# Independent Baseline eligibility (schema v0.2 §5). intended_use MUST be
# heldout — development data never supports unseen-data claims.
BASELINE_ELIGIBILITY = {
    "event_origin": "real",
    "creation_method": "contemporaneous_capture",
    "verbatim_capture": True,
    "ai_exposure": "none",
    "intended_use": "heldout",
}


def valid_verbatim_capture(value: object) -> bool:
    """Type-strict check: JSON true/false or the string \"unknown\" only.

    isinstance(value, bool) must be checked FIRST and int rejected
    explicitly — bool is a subclass of int and 0/1 compare equal to
    False/True, so ordinary equality or set membership would let the
    integers through.
    """
    if isinstance(value, bool):
        return True
    return value == "unknown" and isinstance(value, str)


def manifest_path_for(dataset: Path) -> Path:
    """Sidecar manifest path: dataset.v0.jsonl -> dataset.v0.manifest.json."""
    name = dataset.name
    if name.endswith(".jsonl"):
        name = name[: -len(".jsonl")]
    return dataset.with_name(name + ".manifest.json")


def load_manifest(
    dataset: Path, cls: str, errors: list[str]
) -> dict | None:
    """Load and validate the v0.2 sidecar manifest, or None for legacy v0.1.

    NOTE: the historical boolean pair (ai_raw_exposed/ai_gold_exposed) in
    frozen private manifests maps to ai_exposure for documentation only
    (raw_seen = raw true + gold false; gold_seen = gold true regardless of
    raw; none = both false). The booleans are NOT accepted here — a v0.2
    manifest must declare the canonical ai_exposure enum.
    """
    mpath = manifest_path_for(dataset)
    if not mpath.exists():
        return None
    where = mpath.name
    try:
        m = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{where}: unreadable manifest ({exc})")
        return None
    if not isinstance(m, dict):
        errors.append(f"{where}: manifest must be a JSON object")
        return None

    if m.get("schema_version") != "0.2":
        errors.append(
            f"{where}: schema_version must be '0.2', got "
            f"{m.get('schema_version')!r}"
        )
    if "ai_raw_exposed" in m or "ai_gold_exposed" in m:
        errors.append(
            f"{where}: historical ai_raw_exposed/ai_gold_exposed booleans are "
            "not a v0.2 manifest — declare the canonical ai_exposure enum "
            "(mapping documented in annotation-schema-v0.2 §2.2)"
        )
    checks = [
        ("event_origin", EVENT_ORIGINS),
        ("creation_method", CREATION_METHODS),
        ("ai_exposure", AI_EXPOSURES),
        ("intended_use", INTENDED_USES),
    ]
    for field, allowed in checks:
        if field not in m:
            errors.append(f"{where}: missing required field {field!r}")
        elif m[field] not in allowed:
            errors.append(
                f"{where}: {field} = {m[field]!r} not in {sorted(map(str, allowed))}"
            )
    if "verbatim_capture" not in m:
        errors.append(f"{where}: missing required field 'verbatim_capture'")
    elif not valid_verbatim_capture(m["verbatim_capture"]):
        errors.append(
            f"{where}: verbatim_capture = {m['verbatim_capture']!r} must be "
            f"exactly {VERBATIM_DISPLAY} (JSON booleans; integers 0/1, null "
            "and string booleans are invalid)"
        )

    # Declared provenance is authoritative, but path must agree.
    want_use = INTENDED_USE_BY_CLASS.get(cls)
    if want_use and m.get("intended_use") in INTENDED_USES and (
        m["intended_use"] != want_use
    ):
        errors.append(
            f"{where}: intended_use {m['intended_use']!r} disagrees with the "
            f"storage class {cls!r} (path requires {want_use!r}); split the "
            "corpus rather than mixing uses in one file"
        )
    if cls in PUBLIC_CLASSES and m.get("event_origin") == "real":
        errors.append(
            f"{where}: event_origin 'real' is forbidden in the public "
            "repository — real material lives only in the private workspace"
        )
    return m


def manifest_is_baseline_eligible(m: dict | None) -> tuple[bool, list[str]]:
    """Check the eligibility quintuple; returns (eligible, reasons-if-not)."""
    if m is None:
        return False, [
            "no v0.2 corpus manifest — legacy data contributes zero cases "
            "to v0.2 Baseline readiness"
        ]
    reasons = []
    for field, required in BASELINE_ELIGIBILITY.items():
        value = m.get(field)
        # Type-strict for verbatim_capture: 1 == True in Python, so a plain
        # != comparison would let integer 1 satisfy the True requirement.
        ok = (
            value is True
            if field == "verbatim_capture"
            else value == required
        )
        if not ok:
            reasons.append(f"{field} = {value!r} (required: {required!r})")
    return not reasons, reasons


def worktrees_or_exit() -> list[Path]:
    try:
        return registered_worktrees()
    except WorktreeDiscoveryError as exc:
        print(
            f"ERROR: private-path policy cannot be verified ({exc}). "
            "Refusing to run — this check fails closed.",
            file=sys.stderr,
        )
        sys.exit(2)


def private_root(cli_value: str | None, worktrees: list[Path]) -> Path | None:
    """Resolve the private workspace, refusing any registered worktree."""
    raw = cli_value or os.environ.get("FOREY_PRIVATE_CALIBRATION")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    violation = find_violation("--private-root", path, worktrees)
    if violation is not None:
        print(f"ERROR: {violation}", file=sys.stderr)
        sys.exit(2)
    return path


def known_files(priv: Path | None) -> dict[str, Path]:
    """Class -> path. Public fixtures are in-repo; the rest are private."""
    files = {
        "sample": EXTRACTION_DIR / "dataset.sample.jsonl",
        "synthetic": EXTRACTION_DIR / "calibration" / "synthetic.jsonl",
    }
    if priv is not None:
        # A private synthetic file is equally acceptable; whichever exists
        # is classed 'synthetic' and never counts as real evidence.
        if not files["synthetic"].exists():
            files["synthetic"] = priv / "synthetic.jsonl"
        files.update(
            {
                "reference": priv / "reference.jsonl",
                "real": priv / "dataset.v0.jsonl",
                "heldout": priv / "dataset.heldout.jsonl",
            }
        )
    return files


PUBLIC_CLASSES = {"sample", "synthetic"}


def classify(path: Path, files: dict[str, Path]) -> str:
    for cls, known in files.items():
        try:
            if path.resolve() == known.resolve():
                return cls
        except OSError:
            continue
    return "unclassified"


def assert_readable_location(path: Path, worktrees: list[Path]) -> None:
    """In-repo reads are limited to the public fixture allowlist."""
    if is_public_fixture(path):
        return
    violation = find_violation("dataset", path, worktrees)
    if violation is None:
        return
    print(f"ERROR: {violation}", file=sys.stderr)
    print(
        "Only the public fixtures (dataset.sample.jsonl, "
        "calibration/synthetic.jsonl) may be read from inside a worktree.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def check_fact(
    fact: object, where: str, errors: list[str], v2: bool = False
) -> None:
    if not isinstance(fact, dict):
        errors.append(f"{where}: gold fact is not an object")
        return
    ftype = fact.get("type")
    if ftype not in TYPES:
        errors.append(
            f"{where}: type {ftype!r} not in closed ontology {sorted(TYPES)}"
        )
    if v2 and "fact_category" in fact:
        category = fact["fact_category"]
        if ftype != "site_log_fact":
            errors.append(
                f"{where}: fact_category is only permitted on site_log_fact "
                f"(found on {ftype!r}) — it is metadata inside that type, "
                "never a classification of Tasks or Potential Variations "
                "(DEC-ONTOLOGY-001 Amendment 10)"
            )
        elif category not in FACT_CATEGORIES:
            errors.append(
                f"{where}: fact_category {category!r} not in the closed list "
                f"{sorted(FACT_CATEGORIES)} (generic 'issue' is deliberately "
                "not a category)"
            )
    if not isinstance(fact.get("summary"), str) or not fact["summary"].strip():
        errors.append(f"{where}: missing/empty summary")
    attrs = fact.get("attrs", {})
    if not isinstance(attrs, dict):
        errors.append(f"{where}: attrs is not an object")
        return
    for key, val in attrs.items():
        if not isinstance(val, dict) or "support" not in val or "v" not in val:
            errors.append(
                f"{where}: attr {key!r} must be an object with 'v' and 'support'"
            )
            continue
        if val["support"] not in SUPPORT:
            errors.append(
                f"{where}: attr {key!r} support {val['support']!r} not in "
                f"{sorted(SUPPORT)}"
            )
        if val["support"] == "unknown" and val["v"] is not None:
            errors.append(
                f"{where}: attr {key!r} is 'unknown' but v is not null"
            )


def check_line(
    d: dict,
    where: str,
    cls: str,
    errors: list[str],
    warnings: list[str],
    v2: bool = False,
) -> dict:
    """Validate one parsed line; return facts about it for the summary."""
    info = {"labelled": False, "multi_job": False}

    cid = d.get("id")
    if not isinstance(cid, str) or not cid.strip():
        errors.append(f"{where}: missing id")
        cid = "?"
    info["id"] = cid

    prefix_rules = {"reference": "REF-", "synthetic": "SYN-", "real": "R-",
                    "heldout": "R-", "sample": "SAMPLE-"}
    want = prefix_rules.get(cls)
    if want and isinstance(cid, str) and not cid.startswith(want):
        warnings.append(
            f"{where}: id {cid!r} does not carry the {want!r} prefix "
            f"expected for class {cls!r}"
        )

    if not isinstance(d.get("utterance"), str) or not d["utterance"].strip():
        errors.append(f"{where}: missing/empty utterance")
    if d.get("lang") not in LANGS:
        errors.append(f"{where}: lang {d.get('lang')!r} not in {sorted(LANGS)}")
    info["lang"] = d.get("lang")

    ctx = d.get("context")
    if not isinstance(ctx, dict):
        errors.append(f"{where}: missing context object")
    else:
        if not isinstance(ctx.get("reference_time"), str):
            errors.append(f"{where}: context.reference_time missing")
        job = ctx.get("job")
        job_present = isinstance(job, dict) and isinstance(job.get("name"), str)
        if v2:
            # v0.2: explicit two-state job context. There is deliberately no
            # 'suggested' — a suggested Job is never authoritative extractor
            # input (DEC-JOB-ATTR-001; suggestions are product telemetry).
            state = ctx.get("job_state")
            if state not in JOB_STATES:
                errors.append(
                    f"{where}: context.job_state {state!r} must be one of "
                    f"{sorted(JOB_STATES)} (v0.2 requires an explicit state)"
                )
            elif state == "confirmed" and not job_present:
                errors.append(
                    f"{where}: job_state 'confirmed' requires context.job "
                    "with a name — a confirmed state without a Job is a "
                    "contradiction"
                )
            elif state == "unassigned" and job is not None:
                errors.append(
                    f"{where}: job_state 'unassigned' requires context.job "
                    "to be absent or null — a Job present without human "
                    "confirmation must not masquerade as context"
                )
        else:
            # v0.1 (legacy): job unconditionally required, as originally
            # specified. Existing job-present records remain valid legacy
            # confirmed-context cases.
            if not job_present:
                errors.append(
                    f"{where}: context.job.name missing "
                    "(user-confirmed Job is required input)"
                )

    meta = d.get("meta")
    if not isinstance(meta, dict):
        errors.append(f"{where}: missing meta object")
        meta = {}
    if cls in {"real", "heldout"} and meta.get("privacy") != "scrubbed":
        errors.append(
            f"{where}: real capture without meta.privacy == 'scrubbed' — "
            "must not enter the repository unscrubbed (schema §6)"
        )
    if cls == "heldout" and not v2 and meta.get("held_out") is not True:
        warnings.append(f"{where}: held-out file line lacks meta.held_out: true")
    if v2:
        modality = meta.get("modality")
        if modality not in MODALITIES:
            errors.append(
                f"{where}: meta.modality {modality!r} must be one of "
                f"{sorted(MODALITIES)} — modality is how the evidence was "
                "captured; provenance comes from the corpus manifest"
            )
        routing = meta.get("routing_case")
        if routing is not None:
            if routing not in ROUTING_CASES:
                errors.append(
                    f"{where}: meta.routing_case {routing!r} not in "
                    f"{sorted(ROUTING_CASES)}"
                )
            elif routing == "multi_job":
                info["multi_job"] = True
                if d.get("context", {}).get("job_state") != "unassigned":
                    errors.append(
                        f"{where}: routing_case 'multi_job' requires "
                        "job_state 'unassigned' — multi-Job evidence must "
                        "never be forced into one Job; it awaits human "
                        "splitting/assignment"
                    )
    # Public fixtures (sample + synthetic) live in the repo, so they must
    # never claim a real-capture provenance: 'founder_voice' on a public
    # item either mislabels a fabricated example (confusing) or means a
    # real capture is sitting in a public repo (a leak). Private real and
    # held-out cases legitimately keep 'founder_voice'.
    if cls in PUBLIC_CLASSES and meta.get("source") == "founder_voice":
        errors.append(
            f"{where}: public {cls} fixture claims source 'founder_voice' — "
            "a public fixture must never carry real-capture provenance. Use "
            "'synthetic_fixture' if it is fabricated; if it is a real "
            "capture it does not belong in this repository (schema §6)."
        )

    if "gold" not in d:
        errors.append(f"{where}: missing gold key (use null before labelling)")
    else:
        gold = d["gold"]
        if gold is None:
            pass  # pre-label state — valid
        elif isinstance(gold, dict):
            facts = gold.get("facts")
            if not isinstance(facts, list):
                errors.append(f"{where}: gold.facts must be an array")
            else:
                for i, fact in enumerate(facts):
                    check_fact(fact, f"{where} fact[{i}]", errors, v2=v2)
            mni = gold.get("must_not_infer")
            if mni is not None:
                if not isinstance(mni, list):
                    errors.append(
                        f"{where}: gold.must_not_infer must be an array of strings"
                    )
                else:
                    # Non-string entries are unhashable downstream (label_diff
                    # set-diffs this field), so catch them here rather than as
                    # a traceback later.
                    bad = [x for x in mni if not isinstance(x, str)]
                    if bad:
                        errors.append(
                            f"{where}: gold.must_not_infer must contain only "
                            f"strings (found {type(bad[0]).__name__})"
                        )
            # 'labelled' means a gold object is PRESENT and structurally
            # valid. It says nothing about independence, blind relabel,
            # disagreement resolution or freeze — those are human facts.
            info["labelled"] = True
        else:
            errors.append(f"{where}: gold must be null or an object")

    return info


def validate_file(
    path: Path, files: dict[str, Path]
) -> tuple[str, list[dict], list[str], list[str], dict | None]:
    cls = classify(path, files)
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[dict] = []
    seen_ids: set[str] = set()

    manifest = load_manifest(path, cls, errors)
    v2 = manifest is not None
    if not v2:
        warnings.append(
            f"{path.name}: no v0.2 corpus manifest "
            f"({manifest_path_for(path).name}) — validated as legacy v0.1; "
            "contributes zero cases to v0.2 Baseline readiness"
        )

    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        where = f"{path.name}:{lineno}"
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: invalid JSON ({exc.msg})")
            continue
        info = check_line(d, where, cls, errors, warnings, v2=v2)
        if info["id"] in seen_ids:
            errors.append(f"{where}: duplicate id {info['id']!r}")
        seen_ids.add(info["id"])
        infos.append(info)

    return cls, infos, errors, warnings, manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument(
        "--private-root",
        default=None,
        help="Private calibration workspace (or $FOREY_PRIVATE_CALIBRATION). "
             "Must be outside this repository.",
    )
    ap.add_argument(
        "--baseline-structure-ready",
        action="store_true",
        help=(
            "STRUCTURAL held-out gate: >=30 structurally valid labelled "
            "cases in a fully Baseline-eligible HELD-OUT corpus (schema "
            "v0.2 §5). Development data never counts. Does NOT certify "
            "Baseline v0.1."
        ),
    )
    args = ap.parse_args()

    worktrees = worktrees_or_exit()
    priv = private_root(args.private_root, worktrees)
    files = known_files(priv)

    if args.baseline_structure_ready:
        if priv is None:
            print(
                "STRUCTURAL HELD-OUT GATE: NOT MET - no private workspace "
                "given (--private-root or $FOREY_PRIVATE_CALIBRATION). Real "
                f"cases never live in this repo, so 0/{MINIMUM_REAL_CASES} "
                "is the in-repo answer by policy."
            )
            return 1
        # Independent Baseline evidence comes ONLY from the held-out corpus
        # (schema v0.2 §5). The development corpus (dataset.v0.jsonl) is for
        # schema/prompt calibration and contributes zero here, always.
        heldout = files["heldout"]
        if not heldout.exists():
            print(
                f"STRUCTURAL HELD-OUT GATE: NOT MET - {heldout} does not "
                f"exist (0/{MINIMUM_REAL_CASES} eligible held-out cases). "
                "Development data cannot substitute."
            )
            return 1
        cls, infos, errors, _, manifest = validate_file(heldout, files)
        if errors:
            print(
                "STRUCTURAL HELD-OUT GATE: NOT MET - "
                f"{len(errors)} structural error(s) first:"
            )
            for e in errors:
                print(f"  - {e}")
            return 1
        eligible, reasons = manifest_is_baseline_eligible(manifest)
        if not eligible:
            print(
                "STRUCTURAL HELD-OUT GATE: NOT MET - corpus is not "
                "Baseline-eligible (contributes zero cases):"
            )
            for r in reasons:
                print(f"  - {r}")
            return 1
        labelled = sum(
            1 for i in infos if i["labelled"] and not i["multi_job"]
        )
        excluded = sum(1 for i in infos if i["multi_job"])
        if excluded:
            print(
                f"note: {excluded} multi_job-routed case(s) excluded from "
                "the single-Job extraction Baseline count"
            )
        if labelled < MINIMUM_REAL_CASES:
            print(
                f"STRUCTURAL HELD-OUT GATE: NOT MET - {labelled}/"
                f"{MINIMUM_REAL_CASES} eligible labelled held-out cases"
            )
            return 1
        print(
            f"STRUCTURAL HELD-OUT GATE: MET - {labelled} held-out cases "
            f"carry structurally valid labels (>= {MINIMUM_REAL_CASES}) in "
            "a Baseline-eligible corpus (real, contemporaneous, verbatim, "
            "ai_exposure none, intended_use heldout)."
        )
        print()
        print(
            "This gate checks STRUCTURE and DECLARED PROVENANCE only. It "
            "does NOT verify that the founder labelled independently, that "
            "the one-week blind relabel happened, that disagreements were "
            "resolved, or that the dataset is frozen. Baseline v0.1 is NOT "
            "approved by this output - it requires the founder's explicit "
            "confirmation of those steps."
        )
        return 0

    paths = args.files or [p for p in files.values() if p.exists()]
    if not paths:
        print("ERROR: no dataset files found or given", file=sys.stderr)
        return 2

    any_errors = False
    for path in paths:
        assert_readable_location(path, worktrees)
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 2
        cls, infos, errors, warnings, manifest = validate_file(path, files)
        labelled = sum(1 for i in infos if i["labelled"])
        unlabelled = len(infos) - labelled
        langs = {}
        for i in infos:
            langs[i["lang"]] = langs.get(i["lang"], 0) + 1
        public = " (public fixture - never counts as real evidence)" if cls in PUBLIC_CLASSES else ""
        version = "v0.2" if manifest is not None else "v0.1-legacy"
        print(
            f"{path.as_posix()}  [class: {cls}, {version}]{public}  "
            f"lines: {len(infos)}  labelled: {labelled}  "
            f"unlabelled: {unlabelled}  langs: {langs}"
        )
        for w in warnings:
            print(f"  WARN  {w}")
        for e in errors:
            print(f"  ERROR {e}")
        any_errors = any_errors or bool(errors)

    print()
    print(
        "NOTE: counts above are per-class by design; they are never summed "
        "into one figure (reference/synthetic/real are different evidence). "
        "'labelled' means a gold object is present and structurally valid - "
        "not that it was produced independently, blind-relabelled, or frozen."
    )
    return 1 if any_errors else 0


if __name__ == "__main__":
    sys.exit(main())
