#!/usr/bin/env python3
"""Diff two founder-produced label sets after the blind relabel.

Mechanical comparison only — it reports WHERE the two passes disagree
(missing/extra facts, type changes, attribute value/support changes) and
emits a disagreement-report skeleton for the founder to resolve into
worked examples (schema §8/§10). It never judges which pass is right.

Both inputs are JSONL in the schema §2 line format with non-null gold.
Cases are matched by id (use label_diff after re-attaching ids to the
relabel pass via the private order mapping from blind_shuffle).

**Both inputs and the report output must live outside every registered
Git worktree** — the report quotes verbatim utterances and both gold
label sets. The check runs before anything is read or written, fails
closed, and leaves no partial output (see path_policy.py).

Usage:
  python evals/extraction/tools/label_diff.py FIRST.jsonl SECOND.jsonl \
      [--out C:/private/disagreements.md]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from path_policy import guard_private_paths  # noqa: E402


class DatasetError(RuntimeError):
    """Controlled load failure — reported without a traceback."""


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise DatasetError(f"{path} not found")
    out: dict[str, dict] = {}
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                f"{path.name}:{lineno}: invalid JSON ({exc.msg})"
            ) from None
        if not isinstance(d, dict):
            raise DatasetError(f"{path.name}:{lineno}: line is not an object")
        cid = d.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise DatasetError(f"{path.name}:{lineno}: missing string 'id'")
        if cid in out:
            raise DatasetError(
                f"{path.name}:{lineno}: duplicate case id {cid!r} — "
                "refusing to silently overwrite the earlier line"
            )
        if d.get("gold") is None:
            # Skipping would produce a comparison that looks complete but
            # silently omits cases — the worst possible failure mode for a
            # self-consistency check.
            raise DatasetError(
                f"{path.name}:{lineno}: case {cid} has 'gold': null — this "
                "label set is incomplete and cannot be compared"
            )
        out[cid] = d
    return out


def fact_key(fact: dict) -> str:
    """Loose alignment key: type + normalized summary head."""
    summary = (fact.get("summary") or "").strip().lower()
    return f"{fact.get('type')}::{summary[:40]}"


def index_facts(cid: str, side: str, gold: dict) -> dict[str, dict]:
    """Key facts for alignment, refusing collisions.

    Two facts in one case can collapse to the same key (same type, same
    first 40 summary characters). Building a dict would silently drop the
    earlier one and hide a real disagreement, so a collision is a
    controlled failure instead. Proper semantic matching is out of scope
    here — refusing to guess is the safe minimum.
    """
    indexed: dict[str, dict] = {}
    for fact in gold.get("facts", []) or []:
        if not isinstance(fact, dict):
            raise DatasetError(f"{cid} ({side}): gold fact is not an object")
        key = fact_key(fact)
        if key in indexed:
            raise DatasetError(
                f"{cid} ({side} pass): two facts share the alignment key "
                f"{key!r}. They cannot be matched unambiguously and one "
                "would be silently dropped — disambiguate the summaries "
                "(or the type) and re-run"
            )
        indexed[key] = fact
    return indexed


def diff_case(cid: str, a: dict, b: dict) -> list[str]:
    notes: list[str] = []
    fa = index_facts(cid, "FIRST", a["gold"])
    fb = index_facts(cid, "SECOND", b["gold"])

    for key in fa.keys() - fb.keys():
        notes.append(f"fact only in FIRST pass: {key}")
    for key in fb.keys() - fa.keys():
        notes.append(f"fact only in SECOND pass: {key}")

    for key in fa.keys() & fb.keys():
        attrs_a = fa[key].get("attrs", {})
        attrs_b = fb[key].get("attrs", {})
        for attr in attrs_a.keys() - attrs_b.keys():
            notes.append(f"{key}: attr {attr!r} only in FIRST")
        for attr in attrs_b.keys() - attrs_a.keys():
            notes.append(f"{key}: attr {attr!r} only in SECOND")
        for attr in attrs_a.keys() & attrs_b.keys():
            va, vb = attrs_a[attr], attrs_b[attr]
            if va.get("v") != vb.get("v"):
                notes.append(
                    f"{key}: attr {attr!r} value {va.get('v')!r} -> {vb.get('v')!r}"
                )
            if va.get("support") != vb.get("support"):
                notes.append(
                    f"{key}: attr {attr!r} support "
                    f"{va.get('support')!r} -> {vb.get('support')!r}"
                )

    # The validator enforces a string array; guard here too so malformed
    # input is a controlled error rather than an unhashable-type traceback.
    def _mni(side: str, gold: dict) -> set[str]:
        raw = gold.get("must_not_infer") or []
        if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
            raise DatasetError(
                f"{cid} ({side} pass): gold.must_not_infer must be an array "
                "of strings"
            )
        return set(raw)

    mni_a = _mni("FIRST", a["gold"])
    mni_b = _mni("SECOND", b["gold"])
    for item in mni_a - mni_b:
        notes.append(f"must_not_infer only in FIRST: {item!r}")
    for item in mni_b - mni_a:
        notes.append(f"must_not_infer only in SECOND: {item!r}")

    return notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("first", type=Path)
    ap.add_argument("second", type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="Markdown disagreement report — MUST be outside every worktree")
    args = ap.parse_args()

    # Fail closed BEFORE any read or write: both label sets and the
    # report all contain real utterances and gold.
    guarded = {"first": args.first, "second": args.second}
    if args.out is not None:
        guarded["--out"] = args.out
    guard_private_paths(guarded)

    try:
        first, second = load(args.first), load(args.second)
    except DatasetError as exc:
        print(f"ERROR: {exc}. No output written.", file=sys.stderr)
        return 2

    only_first = sorted(first.keys() - second.keys())
    only_second = sorted(second.keys() - first.keys())
    shared = sorted(first.keys() & second.keys())

    # A self-consistency check is only meaningful over the SAME cases.
    # Reporting on the intersection while quietly ignoring the rest would
    # overstate agreement.
    if only_first or only_second:
        print(
            "ERROR: the two passes cover different case sets — refusing to "
            "compare a partial overlap. No output written.",
            file=sys.stderr,
        )
        if only_first:
            print(f"  only in FIRST:  {only_first}", file=sys.stderr)
        if only_second:
            print(f"  only in SECOND: {only_second}", file=sys.stderr)
        return 2

    agreements = 0
    report: list[str] = []
    try:
        diffs = {
            cid: diff_case(cid, first[cid], second[cid]) for cid in shared
        }
    except DatasetError as exc:
        print(f"ERROR: {exc}. No output written.", file=sys.stderr)
        return 2

    for cid in shared:
        notes = diffs[cid]
        if notes:
            report.append(f"## {cid}\n")
            report.append(f"Utterance: {first[cid].get('utterance')}\n")
            for n in notes:
                report.append(f"- {n}")
            report.append("")
            report.append("**Founder resolution (fill in):** ")
            report.append("**Worked example? (yes/no):** ")
            report.append("\n---\n")
        else:
            agreements += 1

    print(f"cases: FIRST={len(first)} SECOND={len(second)} shared={len(shared)}")
    print(f"identical labels: {agreements}/{len(shared)}")
    print(f"cases with disagreements: {len(shared) - agreements}/{len(shared)}")

    if args.out:
        header = (
            "# Blind relabel disagreement report\n\n"
            f"FIRST: {args.first.name} · SECOND: {args.second.name}\n\n"
            "Each disagreement below is resolved by the FOUNDER; resolved\n"
            "cases become worked examples (schema §10).\n\n---\n\n"
        )
        args.out.write_text(header + "\n".join(report), encoding="utf-8")
        print(f"report: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
