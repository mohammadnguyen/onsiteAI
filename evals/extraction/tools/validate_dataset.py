#!/usr/bin/env python3
"""Structural validator for extraction calibration/eval JSONL files.

Checks SHAPE only — never semantics. It validates that lines conform to
annotation-schema-v0.1 (§2 line format, §3 closed type list, §4 gold fact
objects, §5 support levels, §6 privacy marker) and reports per-class
counts. It deliberately refuses to produce one combined total: reference,
synthetic and real captures are different kinds of evidence.

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
      # STRUCTURAL minimum-dataset gate only: exit 0 iff >= 30 real cases
      # carry structurally valid labels. It cannot verify independence,
      # the elapsed week, disagreement resolution, or freeze — Baseline
      # v0.1 still requires explicit founder approval of those steps.

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


def check_fact(fact: object, where: str, errors: list[str]) -> None:
    if not isinstance(fact, dict):
        errors.append(f"{where}: gold fact is not an object")
        return
    ftype = fact.get("type")
    if ftype not in TYPES:
        errors.append(
            f"{where}: type {ftype!r} not in closed ontology {sorted(TYPES)}"
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
    d: dict, where: str, cls: str, errors: list[str], warnings: list[str]
) -> dict:
    """Validate one parsed line; return facts about it for the summary."""
    info = {"labelled": False}

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
        if not (isinstance(job, dict) and isinstance(job.get("name"), str)):
            errors.append(f"{where}: context.job.name missing (user-confirmed Job is required input)")

    meta = d.get("meta")
    if not isinstance(meta, dict):
        errors.append(f"{where}: missing meta object")
        meta = {}
    if cls in {"real", "heldout"} and meta.get("privacy") != "scrubbed":
        errors.append(
            f"{where}: real capture without meta.privacy == 'scrubbed' — "
            "must not enter the repository unscrubbed (schema §6)"
        )
    if cls == "heldout" and meta.get("held_out") is not True:
        warnings.append(f"{where}: held-out file line lacks meta.held_out: true")
    if cls == "synthetic" and meta.get("source") == "founder_voice":
        errors.append(
            f"{where}: synthetic item claims source 'founder_voice' — "
            "synthetic data must never masquerade as real capture"
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
                    check_fact(fact, f"{where} fact[{i}]", errors)
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
) -> tuple[str, list[dict], list[str], list[str]]:
    cls = classify(path, files)
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[dict] = []
    seen_ids: set[str] = set()

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
        info = check_line(d, where, cls, errors, warnings)
        if info["id"] in seen_ids:
            errors.append(f"{where}: duplicate id {info['id']!r}")
        seen_ids.add(info["id"])
        infos.append(info)

    return cls, infos, errors, warnings


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
            "STRUCTURAL minimum-dataset gate: >=30 structurally valid "
            "labelled real cases. Does NOT certify Baseline v0.1."
        ),
    )
    args = ap.parse_args()

    worktrees = worktrees_or_exit()
    priv = private_root(args.private_root, worktrees)
    files = known_files(priv)

    if args.baseline_structure_ready:
        if priv is None:
            print(
                "STRUCTURAL DATASET GATE: NOT MET - no private workspace "
                "given (--private-root or $FOREY_PRIVATE_CALIBRATION). Real "
                f"cases never live in this repo, so 0/{MINIMUM_REAL_CASES} "
                "is the in-repo answer by policy."
            )
            return 1
        real = files["real"]
        if not real.exists():
            print(
                f"STRUCTURAL DATASET GATE: NOT MET - {real} does not exist "
                f"(0/{MINIMUM_REAL_CASES} labelled real cases)"
            )
            return 1
        cls, infos, errors, _ = validate_file(real, files)
        labelled = sum(1 for i in infos if i["labelled"])
        if errors:
            print(
                "STRUCTURAL DATASET GATE: NOT MET - "
                f"{len(errors)} structural error(s) first:"
            )
            for e in errors:
                print(f"  - {e}")
            return 1
        if labelled < MINIMUM_REAL_CASES:
            print(
                f"STRUCTURAL DATASET GATE: NOT MET - {labelled}/"
                f"{MINIMUM_REAL_CASES} labelled real cases"
            )
            return 1
        print(
            f"STRUCTURAL DATASET GATE: MET - {labelled} real cases carry "
            f"structurally valid labels (>= {MINIMUM_REAL_CASES})."
        )
        print()
        print(
            "This gate checks STRUCTURE ONLY. It does NOT verify that the "
            "founder labelled independently, that the one-week blind relabel "
            "happened, that disagreements were resolved, or that the dataset "
            "is frozen. Baseline v0.1 is NOT approved by this output - it "
            "requires the founder's explicit confirmation of those steps."
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
        cls, infos, errors, warnings = validate_file(path, files)
        labelled = sum(1 for i in infos if i["labelled"])
        unlabelled = len(infos) - labelled
        langs = {}
        for i in infos:
            langs[i["lang"]] = langs.get(i["lang"], 0) + 1
        public = " (public fixture - never counts as real evidence)" if cls in PUBLIC_CLASSES else ""
        print(
            f"{path.as_posix()}  [class: {cls}]{public}  lines: {len(infos)}  "
            f"labelled: {labelled}  unlabelled: {unlabelled}  langs: {langs}"
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
