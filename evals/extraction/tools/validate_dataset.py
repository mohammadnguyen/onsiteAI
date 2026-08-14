#!/usr/bin/env python3
"""Structural validator for extraction calibration/eval JSONL files.

Checks SHAPE only — never semantics. It validates that lines conform to
annotation-schema-v0.1 (§2 line format, §3 closed type list, §4 gold fact
objects, §5 support levels, §6 privacy marker) and reports per-class
counts. It deliberately refuses to produce one combined total: reference,
synthetic and real captures are different kinds of evidence.

Real calibration data never lives in this repository (it is public). The
real/held-out/reference/synthetic files live in a private workspace given
by --private-root or $FOREY_PRIVATE_CALIBRATION; only the shipped sample
file is in-repo.

Usage:
  python evals/extraction/tools/validate_dataset.py [FILES...]
      # no FILES: validates the in-repo sample plus every private-root
      # dataset file that exists
  python evals/extraction/tools/validate_dataset.py --baseline-ready \
      --private-root D:/FOREY_PRIVATE_CALIBRATION
      # exit 0 iff >= 30 REAL cases with frozen (non-null) gold exist in
      # <private-root>/dataset.v0.jsonl — the Baseline v0.1 gate.

Exit codes: 0 = pass, 1 = structural violations, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EXTRACTION_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(__file__).resolve().parents[3]

TYPES = {"site_log_fact", "task", "potential_variation"}
SUPPORT = {"explicit", "reasonable", "unknown", "ambiguous"}
LANGS = {"en", "zh", "mixed"}

BASELINE_MINIMUM = 30


def private_root(cli_value: str | None) -> Path | None:
    """Resolve the private workspace, refusing any path inside the repo."""
    raw = cli_value or os.environ.get("FOREY_PRIVATE_CALIBRATION")
    if not raw:
        return None
    path = Path(raw).resolve()
    root = REPO_ROOT.resolve()
    if path == root or root in path.parents:
        print(
            f"ERROR: private root {path} is inside the repository ({root}). "
            "Real captures and gold labels must never enter this repo.",
            file=sys.stderr,
        )
        sys.exit(2)
    return path


def known_files(priv: Path | None) -> dict[str, Path]:
    files = {"sample": EXTRACTION_DIR / "dataset.sample.jsonl"}
    if priv is not None:
        files.update(
            {
                "reference": priv / "reference.jsonl",
                "synthetic": priv / "synthetic.jsonl",
                "real": priv / "dataset.v0.jsonl",
                "heldout": priv / "dataset.heldout.jsonl",
            }
        )
    return files


def classify(path: Path, files: dict[str, Path]) -> str:
    for cls, known in files.items():
        try:
            if path.resolve() == known.resolve():
                return cls
        except OSError:
            continue
    return "unclassified"


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
    info = {"gold_frozen": False}

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
            if mni is not None and not isinstance(mni, list):
                errors.append(f"{where}: gold.must_not_infer must be an array")
            info["gold_frozen"] = True
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
        "--baseline-ready",
        action="store_true",
        help="Check the Baseline v0.1 precondition (>=30 frozen REAL cases)",
    )
    args = ap.parse_args()

    priv = private_root(args.private_root)
    files = known_files(priv)

    if args.baseline_ready:
        if priv is None:
            print(
                "BASELINE GATE: NOT MET — no private workspace given "
                "(--private-root or $FOREY_PRIVATE_CALIBRATION). Real cases "
                f"never live in this repo, so 0/{BASELINE_MINIMUM} is the "
                "in-repo answer by policy."
            )
            return 1
        real = files["real"]
        if not real.exists():
            print(
                f"BASELINE GATE: NOT MET — {real} does not exist "
                f"(0/{BASELINE_MINIMUM} frozen real cases)"
            )
            return 1
        cls, infos, errors, _ = validate_file(real, files)
        frozen = sum(1 for i in infos if i["gold_frozen"])
        if errors:
            print(f"BASELINE GATE: NOT MET — {len(errors)} structural error(s) first:")
            for e in errors:
                print(f"  - {e}")
            return 1
        if frozen < BASELINE_MINIMUM:
            print(
                f"BASELINE GATE: NOT MET — {frozen}/{BASELINE_MINIMUM} frozen "
                "founder-labelled real cases"
            )
            return 1
        print(f"BASELINE GATE: MET — {frozen} frozen real cases (>= {BASELINE_MINIMUM})")
        return 0

    paths = args.files or [p for p in files.values() if p.exists()]
    if not paths:
        print("ERROR: no dataset files found or given", file=sys.stderr)
        return 2

    any_errors = False
    for path in paths:
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 2
        cls, infos, errors, warnings = validate_file(path, files)
        frozen = sum(1 for i in infos if i["gold_frozen"])
        unlabelled = len(infos) - frozen
        langs = {}
        for i in infos:
            langs[i["lang"]] = langs.get(i["lang"], 0) + 1
        print(
            f"{path.as_posix()}  [class: {cls}]  lines: {len(infos)}  "
            f"gold-frozen: {frozen}  unlabelled: {unlabelled}  langs: {langs}"
        )
        for w in warnings:
            print(f"  WARN  {w}")
        for e in errors:
            print(f"  ERROR {e}")
        any_errors = any_errors or bool(errors)

    print(
        "\nNOTE: counts above are per-class by design; they are never summed "
        "into one figure (reference/synthetic/real are different evidence)."
    )
    return 1 if any_errors else 0


if __name__ == "__main__":
    sys.exit(main())
