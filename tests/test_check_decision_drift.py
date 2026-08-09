"""Fixture tests for scripts/check_decision_drift.py.

ADR-001 §5: a check that has never caught a planted flaw is decoration.
Every test plants exactly one flaw in a miniature charter/product pair and
asserts the gate catches it (or, for green paths, stays silent).

Run: python -m pytest tests/test_check_decision_drift.py -q
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_decision_drift.py"
spec = importlib.util.spec_from_file_location("cdd", SCRIPT)
cdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdd)


CHARTER = """# Mini Charter

<!-- DECISION-REGISTRY:BEGIN -->

### DEC-A-001 — Alpha rule [DECIDED]

Alpha text one.

### DEC-B-001 — Beta rule [DECIDED]

Beta text two.

### DEC-C-001 — Gamma direction [DIRECTION]

Gamma text three.

<!-- DECISION-REGISTRY:END -->

# 1. Alpha — DECIDED

Registry: DEC-A-001

Alpha body prose.

# 2. Beta — DECIDED

Registry: DEC-B-001

Beta body prose.

# 3. Gamma — DIRECTION (original: DECIDED)

Gamma body prose.
"""

SCOPED_CHARTER = """# Mini Scoped Charter

<!-- DECISION-REGISTRY:BEGIN -->

### DEC-A-001 — Alpha rule [DECIDED]
Applies-To: global

Alpha text one.

### DEC-B-001 — Beta rule [DECIDED]
Applies-To: slice-1

Beta text two.

### DEC-H-001 — Future gate [DECIDED]
Applies-To: h5

Future gate text.

<!-- DECISION-REGISTRY:END -->

# 1. Alpha — DECIDED

Registry: DEC-A-001

Alpha body prose.

# 2. Beta — DECIDED

Registry: DEC-B-001

Beta body prose.
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def product_for(charter: Path, ids, ack_override=None, binding_scope=None) -> str:
    """Build a PRODUCT.md referencing ids with correct acks (or an override)."""
    reg = cdd.parse_registry(charter)
    parts = ["# Mini PRODUCT", ""]
    if binding_scope:
        parts += [f"Binding Scope: {binding_scope}", ""]
    for i in ids:
        ack = ack_override.get(i) if ack_override and i in ack_override else (
            reg[i]["hash"] if i in reg else "000000000000")
        parts += [f"## Section {i}", "", f"Source: {i} (test)", f"Ack: {ack}", ""]
    return "\n".join(parts)


def run(charter: Path, product: Path, full=False, scope=None):
    return cdd.run_check(charter, product, require_full_coverage=full,
                         scope_override=scope)


# ---------- green path ----------

def test_green(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001", "DEC-B-001"]))
    failures, warnings, n_refs, n_reg, active, deferred = run(ch, pr, full=True)
    assert failures == [] and warnings == []
    assert (n_refs, n_reg, deferred) == (2, 3, 0)


# ---------- reference integrity ----------

def test_changed_hash_fails_with_reack_instruction(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001", "DEC-B-001"]))
    write(tmp_path, "charter.md", CHARTER.replace("Alpha text one.", "Alpha text 1!"))
    failures, *_ = run(ch, pr)
    assert len(failures) == 1
    assert "changed in the Charter" in failures[0] and "re-acknowledge" in failures[0]


def test_pending_ack_fails_with_fill_instruction(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md",
               product_for(ch, ["DEC-A-001", "DEC-B-001"],
                           ack_override={"DEC-A-001": "PENDING"}))
    failures, *_ = run(ch, pr)
    assert len(failures) == 1
    assert "PENDING" in failures[0] and "Fill with" in failures[0]


def test_missing_ack_line_fails(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    text = product_for(ch, ["DEC-B-001"]) + "\n## Extra\n\nSource: DEC-A-001 (test)\n\nprose\n"
    pr = write(tmp_path, "product.md", text)
    failures, *_ = run(ch, pr)
    assert len(failures) == 1 and "no 'Ack:'" in failures[0]


def test_reference_to_non_decided_fails(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md",
               product_for(ch, ["DEC-A-001", "DEC-B-001", "DEC-C-001"]))
    failures, *_ = run(ch, pr)
    assert any("[DIRECTION]" in f and "DEC-C-001" in f for f in failures)


def test_reference_to_nonexistent_id_fails(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md",
               product_for(ch, ["DEC-A-001", "DEC-B-001", "DEC-Z-001"]))
    failures, *_ = run(ch, pr)
    assert any("DEC-Z-001" in f and "does not exist" in f for f in failures)


# ---------- coverage: default global scope ----------

def test_unreferenced_decided_warns_by_default(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001"]))  # B dropped
    failures, warnings, *_ = run(ch, pr, full=False)
    assert failures == []
    assert any("DEC-B-001" in w for w in warnings)


def test_unreferenced_decided_fails_with_flag(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001"]))  # B dropped
    failures, warnings, *_ = run(ch, pr, full=True)
    assert any("DEC-B-001" in f and "silently left" in f for f in failures)
    assert warnings == []


# ---------- coverage: scoped (Amendment 9) ----------

def test_out_of_scope_unreferenced_passes_with_flag(tmp_path):
    ch = write(tmp_path, "charter.md", SCOPED_CHARTER)
    pr = write(tmp_path, "product.md",
               product_for(ch, ["DEC-A-001", "DEC-B-001"],
                           binding_scope="global, slice-1"))
    failures, warnings, n_refs, n_reg, active, deferred = run(ch, pr, full=True)
    assert failures == [] and warnings == []
    assert active == ["global", "slice-1"] and deferred == 1  # H deferred by design


def test_scope_promotion_forces_acknowledgement(tmp_path):
    ch = write(tmp_path, "charter.md", SCOPED_CHARTER)
    pr = write(tmp_path, "product.md",
               product_for(ch, ["DEC-A-001", "DEC-B-001"],
                           binding_scope="global, slice-1"))
    failures, *_ = run(ch, pr, full=True, scope=["global", "slice-1", "h5"])
    assert any("DEC-H-001" in f for f in failures)


def test_applies_to_excluded_from_hash(tmp_path):
    ch1 = write(tmp_path, "c1.md", SCOPED_CHARTER)
    ch2 = write(tmp_path, "c2.md", SCOPED_CHARTER.replace("Applies-To: h5",
                                                          "Applies-To: slice-1"))
    r1, r2 = cdd.parse_registry(ch1), cdd.parse_registry(ch2)
    assert r1["DEC-H-001"]["hash"] == r2["DEC-H-001"]["hash"]
    assert r1["DEC-H-001"]["scopes"] == ["h5"]
    assert r2["DEC-H-001"]["scopes"] == ["slice-1"]


def test_missing_applies_to_defaults_to_global(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)  # no Applies-To anywhere
    reg = cdd.parse_registry(ch)
    assert all(e["scopes"] == ["global"] for e in reg.values())


# ---------- body coverage (Amendment 7) ----------

def test_decided_heading_without_mapping_fails(tmp_path):
    broken = CHARTER.replace("# 1. Alpha — DECIDED\n\nRegistry: DEC-A-001\n",
                             "# 1. Alpha — DECIDED\n")
    ch = write(tmp_path, "charter.md", broken)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001", "DEC-B-001"]))
    failures, *_ = run(ch, pr)
    assert any("no 'Registry: DEC-" in f for f in failures)


def test_heading_mapped_to_non_decided_fails(tmp_path):
    broken = CHARTER.replace("Registry: DEC-A-001", "Registry: DEC-C-001")
    ch = write(tmp_path, "charter.md", broken)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001", "DEC-B-001"]))
    failures, *_ = run(ch, pr)
    assert any("DEC-C-001" in f and "not DECIDED" in f for f in failures)


def test_original_parenthetical_does_not_require_mapping(tmp_path):
    ch = write(tmp_path, "charter.md", CHARTER)
    pr = write(tmp_path, "product.md", product_for(ch, ["DEC-A-001", "DEC-B-001"]))
    failures, *_ = run(ch, pr)
    assert failures == []


# ---------- parse hardening ----------

def test_duplicate_registry_id_exits(tmp_path):
    dup = CHARTER.replace("### DEC-B-001 — Beta rule [DECIDED]",
                          "### DEC-A-001 — Beta rule [DECIDED]")
    ch = write(tmp_path, "charter.md", dup)
    with pytest.raises(SystemExit):
        cdd.parse_registry(ch)


def test_malformed_heading_fails_loud(tmp_path):
    bad = CHARTER.replace("### DEC-B-001 — Beta rule [DECIDED]",
                          "### DEC-B-001 - Beta rule [DECIDED]")  # hyphen, not em dash
    ch = write(tmp_path, "charter.md", bad)
    with pytest.raises(SystemExit):
        cdd.parse_registry(ch)
