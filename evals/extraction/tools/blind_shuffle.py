#!/usr/bin/env python3
"""Produce a blind-relabel worksheet: shuffled cases with gold STRIPPED.

Independence control for the one-week blind relabel (schema §8): the
founder must not see their first-pass labels. This tool takes the
labelled dataset, removes every gold label, shuffles case order
deterministically from a seed, and writes:

  1. a relabel worksheet (markdown) with utterance + frozen context and
     a blank gold template per case — NO prior labels anywhere in it;
  2. an order-mapping JSON (shuffled position -> case id).

**All three paths (dataset, worksheet, mapping) must live outside every
registered Git worktree** — the worksheet carries verbatim utterances and
frozen context, so it is exactly as sensitive as the dataset. The check
runs before anything is read or written, fails closed, and leaves no
partial output (see path_policy.py).

The elapsed week is a human control — this tool does not and cannot
compress it.

Usage:
  python evals/extraction/tools/blind_shuffle.py DATASET.jsonl \
      --seed 20260821 --worksheet OUT.md --mapping C:/private/map.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from path_policy import guard_private_paths  # noqa: E402


def render_case(position: int, d: dict) -> str:
    ctx = d.get("context", {})
    lines = [
        f"## Case {position}",
        "",
        f"**Utterance ({d.get('lang', '?')}):**",
        "",
        f"> {d['utterance']}",
        "",
        "**Frozen context:**",
        "",
        f"- reference_time: {ctx.get('reference_time')}",
        f"- job: {ctx.get('job', {}).get('name')}",
        f"- people: {json.dumps(ctx.get('people', []), ensure_ascii=False)}",
        f"- suppliers: {json.dumps(ctx.get('suppliers', []), ensure_ascii=False)}",
        f"- locations: {json.dumps(ctx.get('locations', []), ensure_ascii=False)}",
    ]
    if ctx.get("notes"):
        lines.append(f"- notes: {ctx['notes']}")
    lines += [
        "",
        "**Gold (fill in — do not consult any earlier labels):**",
        "",
        "```json",
        json.dumps(
            {
                "facts": [
                    {
                        "type": "site_log_fact | task | potential_variation",
                        "summary": "",
                        "attrs": {
                            "<attr>": {"v": "", "support": "explicit | reasonable | unknown | ambiguous"}
                        },
                    }
                ],
                "must_not_infer": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--seed", required=True, type=int,
                    help="Shuffle seed (record it; makes the order reproducible)")
    ap.add_argument("--worksheet", required=True, type=Path)
    ap.add_argument("--mapping", required=True, type=Path,
                    help="Order-mapping JSON path — MUST be outside every worktree")
    args = ap.parse_args()

    # Fail closed BEFORE reading or writing anything: dataset, worksheet
    # and mapping all carry (or reveal) real capture content.
    guard_private_paths(
        {
            "dataset": args.dataset,
            "--worksheet": args.worksheet,
            "--mapping": args.mapping,
        }
    )
    mapping_path = args.mapping.resolve()

    if not args.dataset.exists():
        print(f"ERROR: dataset {args.dataset} not found", file=sys.stderr)
        return 2

    cases = []
    for lineno, raw in enumerate(
        args.dataset.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"ERROR: {args.dataset.name}:{lineno}: invalid JSON ({exc.msg}). "
                "No output written.",
                file=sys.stderr,
            )
            return 2
        if not isinstance(d, dict) or not isinstance(d.get("id"), str):
            print(
                f"ERROR: {args.dataset.name}:{lineno}: line has no string 'id'. "
                "No output written.",
                file=sys.stderr,
            )
            return 2
        if d.get("gold") is None:
            print(
                f"WARN: {args.dataset.name}:{lineno} ({d.get('id')}) has no "
                "gold yet — included anyway (relabel treats all the same)",
                file=sys.stderr,
            )
        cases.append(d)

    if not cases:
        print("ERROR: dataset is empty", file=sys.stderr)
        return 2

    ids = [d["id"] for d in cases]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        print(
            f"ERROR: duplicate case id(s) {duplicates} — refusing to shuffle "
            "a dataset whose ids cannot be re-attached unambiguously. "
            "No output written.",
            file=sys.stderr,
        )
        return 2

    rng = random.Random(args.seed)
    rng.shuffle(cases)

    header = (
        "# Blind relabel worksheet\n\n"
        f"Source: {args.dataset.name} · cases: {len(cases)} · seed: {args.seed}\n\n"
        "Rules: label every case fresh using annotation-schema-v0.1 only.\n"
        "Do NOT open the original dataset, the first-pass labels, or the\n"
        "order mapping until every case below is labelled.\n\n---\n\n"
    )
    body = "".join(
        render_case(pos, d) for pos, d in enumerate(cases, start=1)
    )
    args.worksheet.parent.mkdir(parents=True, exist_ok=True)
    args.worksheet.write_text(header + body, encoding="utf-8")

    mapping = {str(pos): d["id"] for pos, d in enumerate(cases, start=1)}
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        json.dumps({"seed": args.seed, "source": args.dataset.name,
                    "order": mapping}, indent=2),
        encoding="utf-8",
    )

    print(f"worksheet: {args.worksheet} ({len(cases)} cases, gold stripped)")
    print(f"mapping:   {mapping_path} (keep private until diff time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
