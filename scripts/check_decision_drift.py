#!/usr/bin/env python3
"""Decision drift check (L1 deterministic gate).

Verifies that every decision PRODUCT.md claims to implement still exists in
the Charter's Decision Registry, is still DECIDED, and has not changed since
PRODUCT.md last acknowledged it.

Checks performed:
  1. Reference integrity — every 'Source: DEC-*' in PRODUCT.md must exist in
     the registry, be [DECIDED], and carry an 'Ack:' equal to the current
     normalized hash (sha256 of lowercased, whitespace-collapsed registry
     text, first 12 hex chars).
  2. Body coverage (Amendment 7) — every numbered body heading labelled
     DECIDED must carry a 'Registry: DEC-...' mapping line pointing at
     existing DECIDED entries. '(original: ...)' parentheticals are ignored.
  3. Scoped full coverage (Amendment 9, --require-full-coverage) — every
     DECIDED entry whose 'Applies-To' scopes intersect the active binding
     scope must be referenced by PRODUCT.md. The active scope comes from
     PRODUCT.md's 'Binding Scope:' declaration (or the --scope override).
     Out-of-scope DECIDED entries are deferred by design and reported only
     as a count. Without the flag, in-scope gaps warn; CI runs WITH the flag.

Registry metadata:
  - 'Applies-To: global, slice-1, h3, ...' may appear as the first line under
    an entry heading. It is ROUTING METADATA, not a fourth authority level,
    and is EXCLUDED from the normalized hash: re-scoping an entry never
    invalidates existing acknowledgements — the coverage check itself
    enforces the consequences of a scope change. Missing Applies-To defaults
    to 'global' (fail-safe: over-require, never silently exempt).

Normative vs explanatory (deliberate design):
  - Registry entries are normative and hash-protected. Body prose is
    explanatory context and is NOT hash-protected. Body DECIDED labels must
    map to a valid Registry ID, but prose equivalence is not machine-
    verified: a green check does not mean the body text is semantically
    unchanged.
  - Registry heading syntax uses a literal em dash ' — ' and a '[STATUS]'
    suffix. A malformed heading that still starts like an entry ('### DEC-')
    fails loud (exit 2) instead of silently dropping the entry.

Behaviour on registry change: the check FAILS and asks for re-acknowledgement
(update the Ack in the same commit). It does not forbid Charter changes; it
forbids *unacknowledged* ones.

Usage:
  python scripts/check_decision_drift.py                          # local
  python scripts/check_decision_drift.py --require-full-coverage  # CI
  python scripts/check_decision_drift.py --require-full-coverage --scope global,slice-1
  python scripts/check_decision_drift.py --print-hashes           # maintenance

Exit codes: 0 = pass, 1 = failures found, 2 = parse/config error.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

CHARTER_PATH = Path("docs/product/forey-charter-v1.0.md")
PRODUCT_PATH = Path("docs/product/PRODUCT.md")

REGISTRY_BEGIN = "<!-- DECISION-REGISTRY:BEGIN -->"
REGISTRY_END = "<!-- DECISION-REGISTRY:END -->"

ENTRY_RE = re.compile(
    r"^###\s+(DEC-[A-Z0-9-]+)\s+—\s+(.*?)\s+\[(DECIDED|DIRECTION|NOT NOW)\]\s*$"
)
ENTRY_LIKE_RE = re.compile(r"^###\s+DEC-")
APPLIES_RE = re.compile(r"^Applies-To:\s*([a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)\s*$")
SOURCE_RE = re.compile(r"^\s*Source:\s*(DEC-[A-Z0-9-]+)\b")
ACK_RE = re.compile(r"^\s*Ack:\s*([0-9a-fA-F]{12}|PENDING)\s*$")
BINDING_SCOPE_RE = re.compile(r"^Binding Scope:\s*(.+?)\s*$", re.MULTILINE)


def normalized_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def parse_registry(charter_path: Path) -> dict[str, dict]:
    raw = charter_path.read_text(encoding="utf-8")
    try:
        block = raw.split(REGISTRY_BEGIN, 1)[1].split(REGISTRY_END, 1)[0]
    except IndexError:
        print(f"ERROR: registry markers not found in {charter_path}", file=sys.stderr)
        sys.exit(2)

    entries: dict[str, dict] = {}
    current_id = None
    awaiting_meta = False
    buf: list[str] = []

    def flush():
        nonlocal current_id, buf
        if current_id is not None:
            text = "\n".join(buf)
            if not text.strip():
                print(f"ERROR: registry entry {current_id} has empty text", file=sys.stderr)
                sys.exit(2)
            entries[current_id]["hash"] = normalized_hash(text)
        current_id, buf = None, []

    for line in block.splitlines():
        m = ENTRY_RE.match(line)
        if m:
            flush()
            dec_id, title, status = m.group(1), m.group(2), m.group(3)
            if dec_id in entries:
                print(f"ERROR: duplicate registry ID {dec_id}", file=sys.stderr)
                sys.exit(2)
            entries[dec_id] = {"title": title, "status": status,
                               "hash": None, "scopes": ["global"]}
            current_id = dec_id
            awaiting_meta = True
        elif ENTRY_LIKE_RE.match(line):
            print(f"ERROR: malformed registry heading (needs ' — ' em dash and "
                  f"'[STATUS]'): {line.strip()}", file=sys.stderr)
            sys.exit(2)
        elif current_id is not None:
            if awaiting_meta and line.strip():
                awaiting_meta = False
                am = APPLIES_RE.match(line.strip())
                if am:
                    entries[current_id]["scopes"] = [
                        s.strip() for s in am.group(1).split(",")]
                    continue
            buf.append(line)
    flush()

    if not entries:
        print("ERROR: registry parsed but contains no entries", file=sys.stderr)
        sys.exit(2)
    return entries


def parse_product_refs(product_path: Path) -> list[tuple[str, str, int]]:
    """Return list of (decision_id, ack_hash, line_number_of_source)."""
    refs: list[tuple[str, str, int]] = []
    lines = product_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = SOURCE_RE.match(line)
        if m:
            dec_id = m.group(1)
            ack = None
            for j in (i + 1, i + 2):
                if j < len(lines):
                    a = ACK_RE.match(lines[j])
                    if a:
                        ack = a.group(1).lower()
                        break
            refs.append((dec_id, ack if ack is not None else "MISSING", i + 1))
    return refs


def parse_binding_scope(product_path: Path) -> list[str] | None:
    m = BINDING_SCOPE_RE.search(product_path.read_text(encoding="utf-8"))
    if not m:
        return None
    return [s.strip() for s in m.group(1).split(",") if s.strip()]


def check_body_coverage(charter_path: Path, registry: dict[str, dict]) -> list[str]:
    """Amendment 7 rule: every h1 body heading labelled DECIDED must carry a
    'Registry: DEC-...' mapping line, and every mapped ID must exist with
    status DECIDED. '(original: ...)' parentheticals are ignored when deciding
    whether a heading claims DECIDED."""
    raw = charter_path.read_text(encoding="utf-8")
    body = raw.split(REGISTRY_END, 1)[1]
    lines = body.splitlines()
    problems: list[str] = []
    reg_line_re = re.compile(
        r"^Registry:\s*(DEC-[A-Z0-9-]+(?:\s*,\s*DEC-[A-Z0-9-]+)*)\s*$")
    for idx, line in enumerate(lines):
        if not re.match(r"^#\s+\d+\.", line):
            continue
        effective = re.sub(r"\(original:[^)]*\)", "", line)
        if "DECIDED" not in effective:
            continue
        ids, seen = None, 0
        for j in range(idx + 1, min(idx + 8, len(lines))):
            nxt = lines[j].strip()
            if not nxt:
                continue
            if nxt.startswith("#"):
                break  # next heading — do not leak into the following section
            seen += 1
            m = reg_line_re.match(nxt)
            if m:
                ids = [s.strip() for s in m.group(1).split(",")]
                break
            if seen >= 3:
                break
        heading = line.strip()
        if ids is None:
            problems.append(
                f"charter body coverage: '{heading}' is labelled DECIDED but has "
                f"no 'Registry: DEC-...' mapping line (Amendment 7 rule). Add the "
                f"mapping or downgrade the label.")
            continue
        for dec_id in ids:
            if dec_id not in registry:
                problems.append(
                    f"charter body coverage: '{heading}' maps to {dec_id}, "
                    f"which does not exist in the Decision Registry.")
            elif registry[dec_id]["status"] != "DECIDED":
                problems.append(
                    f"charter body coverage: '{heading}' maps to {dec_id}, "
                    f"which is [{registry[dec_id]['status']}], not DECIDED.")
    return problems


def run_check(charter: Path, product: Path, require_full_coverage: bool = False,
              scope_override: list[str] | None = None):
    """Core check. Returns (failures, warnings, n_refs, n_registry, active_scopes, deferred)."""
    registry = parse_registry(charter)
    refs = parse_product_refs(product)
    active = scope_override if scope_override is not None else parse_binding_scope(product)

    failures: list[str] = list(check_body_coverage(charter, registry))
    warnings: list[str] = []

    for dec_id, ack, line_no in refs:
        if dec_id not in registry:
            failures.append(
                f"{product}:{line_no}: references {dec_id}, which does not exist in the "
                f"Charter registry. Either restore the registry entry or remove/replace "
                f"this PRODUCT.md section.")
            continue
        entry = registry[dec_id]
        if entry["status"] != "DECIDED":
            failures.append(
                f"{product}:{line_no}: {dec_id} is [{entry['status']}] in the Charter, "
                f"but PRODUCT.md treats it as binding. A non-DECIDED item cannot be "
                f"implementation authority — reopen the decision or remove the section.")
        if ack == "MISSING":
            failures.append(
                f"{product}:{line_no}: {dec_id} has no 'Ack:' line. Add: Ack: {entry['hash']}")
        elif ack == "pending":
            failures.append(
                f"{product}:{line_no}: {dec_id} Ack is PENDING. Fill with: Ack: {entry['hash']}")
        elif ack != entry["hash"]:
            failures.append(
                f"{product}:{line_no}: {dec_id} changed in the Charter since PRODUCT.md "
                f"acknowledged it (ack {ack} != current {entry['hash']}). Review the "
                f"change, update this PRODUCT.md section if needed, and set "
                f"Ack: {entry['hash']} in the same commit to re-acknowledge.")

    referenced = {r[0] for r in refs}
    deferred = 0
    for dec_id, e in registry.items():
        if e["status"] != "DECIDED" or dec_id in referenced:
            continue
        in_scope = active is None or bool(set(e["scopes"]) & set(active))
        if in_scope:
            msg = (f"{dec_id} (Applies-To: {', '.join(e['scopes'])}) is DECIDED and "
                   f"inside the active binding scope but not referenced in PRODUCT.md "
                   f"— a binding decision has silently left the implementation authority.")
            if require_full_coverage:
                failures.append(msg + " (--require-full-coverage is on)")
            else:
                warnings.append(msg + " (informational; CI runs with --require-full-coverage)")
        else:
            deferred += 1

    return failures, warnings, len(refs), len(registry), active, deferred


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--charter", default=str(CHARTER_PATH))
    ap.add_argument("--product", default=str(PRODUCT_PATH))
    ap.add_argument("--scope", default=None,
                    help="Comma-separated scope override, e.g. 'global,slice-1'. "
                         "Default: read 'Binding Scope:' from PRODUCT.md; if absent, "
                         "ALL DECIDED entries are treated as in-scope (fail-safe).")
    ap.add_argument("--require-full-coverage", action="store_true",
                    help="Fail (not warn) when an in-scope DECIDED registry entry is "
                         "not referenced by PRODUCT.md. CI must use this.")
    ap.add_argument("--print-hashes", action="store_true",
                    help="Print current registry hashes and scopes (for filling/updating Ack lines)")
    args = ap.parse_args()

    charter = Path(args.charter)
    product = Path(args.product)
    for p in (charter, product) if not args.print_hashes else (charter,):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 2

    if args.print_hashes:
        registry = parse_registry(charter)
        for dec_id, e in registry.items():
            scopes = ",".join(e["scopes"])
            print(f"{dec_id}  [{e['status']:<9}]  {e['hash']}  scopes={scopes:<16}  {e['title']}")
        return 0

    scope_override = ([s.strip() for s in args.scope.split(",") if s.strip()]
                      if args.scope else None)
    failures, warnings, n_refs, n_registry, active, deferred = run_check(
        charter, product, args.require_full_coverage, scope_override)

    if n_refs == 0:
        print(f"ERROR: no 'Source: DEC-...' references found in {product}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"WARNING: {w}")

    scope_txt = ", ".join(active) if active else "ALL (no Binding Scope declared)"
    if failures:
        print(f"\nDECISION DRIFT CHECK: FAIL ({len(failures)} issue(s)) "
              f"[scope: {scope_txt}]\n")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"DECISION DRIFT CHECK: PASS ({n_refs} reference(s) verified against "
          f"{n_registry} registry entries; scope: {scope_txt}; "
          f"{deferred} out-of-scope DECIDED deferred)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
