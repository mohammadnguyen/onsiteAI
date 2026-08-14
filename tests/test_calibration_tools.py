"""Path-policy and gate-honesty tests for the calibration tooling.

These are the persistent regression tests for two properties that are easy
to break and expensive to get wrong:

1. **Private-path policy** — real captures, gold labels, relabel
   worksheets, order mappings and disagreement reports must never be
   writable (or readable) inside ANY registered Git worktree. Refusals
   must fail closed, exit 2, name the offending path, and leave no
   partial output behind.
2. **Gate honesty** — the structural minimum-dataset gate must never
   present itself as Baseline v0.1 approval. Structure is machine-
   checkable; independence, the elapsed week, disagreement resolution and
   freeze are human facts a script cannot verify.

Tools are exercised as subprocesses so exit codes and stderr are the real
CLI contract, not an in-process approximation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "evals" / "extraction" / "tools"
VALIDATE = TOOLS / "validate_dataset.py"
SHUFFLE = TOOLS / "blind_shuffle.py"
DIFF = TOOLS / "label_diff.py"

sys.path.insert(0, str(TOOLS))

from path_policy import (  # noqa: E402
    PrivatePathViolation,
    assert_private_paths,
    is_within,
    registered_worktrees,
)


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def case(cid: str, *, gold: dict | None = None, lang: str = "en") -> dict:
    """A structurally valid line. Content is filler, never a real capture."""
    return {
        "id": cid,
        "utterance": f"structural fixture {cid}",
        "lang": lang,
        "context": {
            "reference_time": "2026-08-14T09:00:00+10:00",
            "job": {"name": "JOB-TEST"},
            "people": [],
            "suppliers": [],
            "locations": [],
        },
        "gold": gold,
        "meta": {
            "source": "test_fixture",
            "collected_at": "2026-08-14",
            "privacy": "scrubbed",
        },
    }


LABELLED = {
    "facts": [
        {
            "type": "site_log_fact",
            "summary": "structural filler fact",
            "attrs": {"location": {"v": "site", "support": "explicit"}},
        }
    ],
    "must_not_infer": [],
}


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def other_worktree() -> Path:
    """A registered worktree that is NOT the one holding these tests."""
    for tree in registered_worktrees():
        if not is_within(REPO_ROOT, tree) and not is_within(tree, REPO_ROOT):
            return tree
    pytest.skip("repository has only one registered worktree")


# ------------------------------------------------- policy: deterministic unit
# The integration test below needs a second real worktree and therefore
# skips on a single-worktree checkout (e.g. a plain CI runner). This unit
# test injects the worktree list instead, so the "any registered worktree,
# not just the current one" rule is verified on EVERY runner, always.


def test_second_registered_worktree_is_rejected_without_real_worktrees(tmp_path):
    first = tmp_path / "wt-primary"
    second = tmp_path / "wt-linked"
    outside = tmp_path / "private"
    for d in (first, second, outside):
        d.mkdir()

    target = second / "relabel" / "worksheet.md"
    with pytest.raises(PrivatePathViolation) as excinfo:
        assert_private_paths({"--worksheet": target}, worktrees=[first, second])

    violation = excinfo.value
    assert violation.label == "--worksheet"
    assert is_within(violation.path, second)
    assert violation.worktree == second
    assert "worktree" in str(violation)

    # A path under neither worktree is accepted.
    assert_private_paths(
        {"--worksheet": outside / "worksheet.md"}, worktrees=[first, second]
    )


def test_first_registered_worktree_is_also_rejected(tmp_path):
    first = tmp_path / "wt-primary"
    second = tmp_path / "wt-linked"
    first.mkdir()
    second.mkdir()

    with pytest.raises(PrivatePathViolation) as excinfo:
        assert_private_paths(
            {"dataset": first / "evals" / "dataset.v0.jsonl"},
            worktrees=[first, second],
        )
    assert excinfo.value.worktree == first


# --------------------------------------------------------------- policy: 1-2


def test_dataset_inside_current_worktree_is_refused(tmp_path):
    inside = REPO_ROOT / "evals" / "extraction" / "should-never-exist.jsonl"
    proc = run(
        SHUFFLE,
        str(inside),
        "--seed", "1",
        "--worksheet", str(tmp_path / "ws.md"),
        "--mapping", str(tmp_path / "map.json"),
    )
    assert proc.returncode == 2
    assert "dataset" in proc.stderr and "worktree" in proc.stderr
    assert not (tmp_path / "ws.md").exists()
    assert not inside.exists()


def test_dataset_inside_another_registered_worktree_is_refused(tmp_path):
    """The primary checkout is a different path — it must be covered too."""
    elsewhere = other_worktree() / "evals" / "should-never-exist.jsonl"
    proc = run(
        SHUFFLE,
        str(elsewhere),
        "--seed", "1",
        "--worksheet", str(tmp_path / "ws.md"),
        "--mapping", str(tmp_path / "map.json"),
    )
    assert proc.returncode == 2
    assert str(other_worktree()) in proc.stderr.replace("/", "\\").replace(
        "\\\\", "\\"
    ) or "worktree" in proc.stderr
    assert not elsewhere.exists()
    assert not (tmp_path / "ws.md").exists()


# --------------------------------------------------------------- policy: 3-4


def test_worksheet_inside_worktree_is_refused(tmp_path):
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", [case("R-0001")])
    worksheet = REPO_ROOT / "evals" / "extraction" / "leaked-worksheet.md"
    proc = run(
        SHUFFLE,
        str(dataset),
        "--seed", "1",
        "--worksheet", str(worksheet),
        "--mapping", str(tmp_path / "map.json"),
    )
    assert proc.returncode == 2
    assert "--worksheet" in proc.stderr
    assert not worksheet.exists()
    assert not (tmp_path / "map.json").exists()


def test_mapping_inside_worktree_is_refused(tmp_path):
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", [case("R-0001")])
    mapping = REPO_ROOT / "evals" / "extraction" / "leaked-mapping.json"
    proc = run(
        SHUFFLE,
        str(dataset),
        "--seed", "1",
        "--worksheet", str(tmp_path / "ws.md"),
        "--mapping", str(mapping),
    )
    assert proc.returncode == 2
    assert "--mapping" in proc.stderr
    assert not mapping.exists()
    assert not (tmp_path / "ws.md").exists()


# ----------------------------------------------------------------- policy: 5


@pytest.mark.parametrize("offender", ["first", "second", "out"])
def test_label_diff_paths_inside_worktree_are_refused(tmp_path, offender):
    first = write_jsonl(tmp_path / "first.jsonl", [case("R-0001", gold=LABELLED)])
    second = write_jsonl(tmp_path / "second.jsonl", [case("R-0001", gold=LABELLED)])
    out = tmp_path / "report.md"
    in_repo = REPO_ROOT / "evals" / "extraction" / f"leaked-{offender}"

    args = [str(first), str(second), "--out", str(out)]
    if offender == "first":
        args[0] = str(in_repo)
    elif offender == "second":
        args[1] = str(in_repo)
    else:
        args[3] = str(in_repo)

    proc = run(DIFF, *args)
    assert proc.returncode == 2
    assert "worktree" in proc.stderr
    assert not in_repo.exists()
    assert not out.exists()


# ----------------------------------------------------------------- policy: 6


def test_private_paths_outside_repo_are_allowed(tmp_path):
    dataset = write_jsonl(
        tmp_path / "dataset.v0.jsonl",
        [case("R-0001", gold=LABELLED), case("R-0002", gold=LABELLED)],
    )
    worksheet = tmp_path / "relabel" / "worksheet.md"
    mapping = tmp_path / "relabel" / "mapping.json"

    proc = run(
        SHUFFLE,
        str(dataset),
        "--seed", "20260821",
        "--worksheet", str(worksheet),
        "--mapping", str(mapping),
    )
    assert proc.returncode == 0, proc.stderr
    assert worksheet.exists() and mapping.exists()
    # Gold must not leak into the worksheet.
    assert "structural filler fact" not in worksheet.read_text(encoding="utf-8")


def test_validator_refuses_non_fixture_file_inside_worktree(tmp_path):
    intruder = REPO_ROOT / "evals" / "extraction" / "dataset.v0.jsonl"
    proc = run(VALIDATE, str(intruder))
    assert proc.returncode == 2
    assert "worktree" in proc.stderr
    assert "public fixture" in proc.stderr.lower()


def test_validator_accepts_public_fixture_in_repo():
    fixture = REPO_ROOT / "evals" / "extraction" / "dataset.sample.jsonl"
    proc = run(VALIDATE, str(fixture))
    assert proc.returncode == 0, proc.stderr
    assert "public fixture" in proc.stdout
    assert "class: sample" in proc.stdout


def test_private_root_inside_worktree_is_refused(tmp_path):
    proc = run(
        VALIDATE,
        "--baseline-structure-ready",
        "--private-root", str(REPO_ROOT / "evals"),
    )
    assert proc.returncode == 2
    assert "--private-root" in proc.stderr


# ---------------------------------------------------------------- gate: 7-8


def test_twentynine_labelled_cases_do_not_pass_the_minimum_gate(tmp_path):
    rows = [case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 30)]
    write_jsonl(tmp_path / "dataset.v0.jsonl", rows)
    proc = run(
        VALIDATE, "--baseline-structure-ready", "--private-root", str(tmp_path)
    )
    assert proc.returncode == 1
    assert "NOT MET" in proc.stdout
    assert "29/30" in proc.stdout


def test_thirty_labelled_cases_pass_structure_only_and_claim_nothing_more(tmp_path):
    rows = [case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 31)]
    write_jsonl(tmp_path / "dataset.v0.jsonl", rows)
    proc = run(
        VALIDATE, "--baseline-structure-ready", "--private-root", str(tmp_path)
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    # It reports a structural fact...
    assert "STRUCTURAL DATASET GATE: MET" in out
    assert "structurally valid labels" in out

    # ...and explicitly disclaims the human process it cannot verify.
    assert "STRUCTURE ONLY" in out
    assert "Baseline v0.1 is NOT approved" in out
    for human_fact in ("independently", "blind relabel", "frozen"):
        assert human_fact in out

    # It must never announce readiness.
    lowered = out.lower()
    assert "baseline v0.1 ready" not in lowered
    assert "baseline ready" not in lowered


# --------------------------------------------------------------- robustness


def test_duplicate_case_ids_fail_rather_than_silently_overwrite(tmp_path):
    rows = [case("R-0001", gold=LABELLED), case("R-0001", gold=LABELLED)]
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", rows)

    validated = run(VALIDATE, str(dataset))
    assert validated.returncode == 1
    assert "duplicate id" in validated.stdout

    shuffled = run(
        SHUFFLE,
        str(dataset),
        "--seed", "1",
        "--worksheet", str(tmp_path / "ws.md"),
        "--mapping", str(tmp_path / "map.json"),
    )
    assert shuffled.returncode == 2
    assert "duplicate" in shuffled.stderr
    assert not (tmp_path / "ws.md").exists()

    diffed = run(DIFF, str(dataset), str(dataset), "--out", str(tmp_path / "r.md"))
    assert diffed.returncode == 2
    assert "duplicate case id" in diffed.stderr
    assert not (tmp_path / "r.md").exists()


def test_malformed_json_is_a_controlled_error_not_a_traceback(tmp_path):
    broken = tmp_path / "dataset.v0.jsonl"
    broken.write_text('{"id": "R-0001", "utterance": ', encoding="utf-8")

    validated = run(VALIDATE, str(broken))
    assert validated.returncode == 1
    assert "invalid JSON" in validated.stdout
    assert "Traceback" not in validated.stderr

    shuffled = run(
        SHUFFLE,
        str(broken),
        "--seed", "1",
        "--worksheet", str(tmp_path / "ws.md"),
        "--mapping", str(tmp_path / "map.json"),
    )
    assert shuffled.returncode == 2
    assert "invalid JSON" in shuffled.stderr
    assert "Traceback" not in shuffled.stderr
    assert not (tmp_path / "ws.md").exists()

    diffed = run(DIFF, str(broken), str(broken), "--out", str(tmp_path / "r.md"))
    assert diffed.returncode == 2
    assert "invalid JSON" in diffed.stderr
    assert "Traceback" not in diffed.stderr
    assert not (tmp_path / "r.md").exists()


# ------------------------------------------- completeness of the label passes


def test_blind_shuffle_refuses_incomplete_first_pass(tmp_path):
    """A blind relabel is meaningless unless pass one is complete."""
    rows = [
        case("R-0001", gold=LABELLED),
        case("R-0002"),  # gold: null
        case("R-0003"),  # gold: null
    ]
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", rows)
    worksheet = tmp_path / "relabel" / "worksheet.md"
    mapping = tmp_path / "relabel" / "mapping.json"

    proc = run(
        SHUFFLE,
        str(dataset),
        "--seed", "1",
        "--worksheet", str(worksheet),
        "--mapping", str(mapping),
    )
    assert proc.returncode == 2
    assert "'gold': null" in proc.stderr
    assert "R-0002" in proc.stderr and "R-0003" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not worksheet.exists()
    assert not mapping.exists()


def test_label_diff_refuses_null_gold(tmp_path):
    first = write_jsonl(
        tmp_path / "first.jsonl",
        [case("R-0001", gold=LABELLED), case("R-0002")],
    )
    second = write_jsonl(
        tmp_path / "second.jsonl",
        [case("R-0001", gold=LABELLED), case("R-0002", gold=LABELLED)],
    )
    out = tmp_path / "report.md"

    proc = run(DIFF, str(first), str(second), "--out", str(out))
    assert proc.returncode == 2
    assert "R-0002" in proc.stderr and "gold" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_label_diff_refuses_mismatched_case_sets(tmp_path):
    first = write_jsonl(
        tmp_path / "first.jsonl",
        [case("R-0001", gold=LABELLED), case("R-0002", gold=LABELLED)],
    )
    second = write_jsonl(
        tmp_path / "second.jsonl",
        [case("R-0001", gold=LABELLED), case("R-0003", gold=LABELLED)],
    )
    out = tmp_path / "report.md"

    proc = run(DIFF, str(first), str(second), "--out", str(out))
    assert proc.returncode == 2
    assert "different case sets" in proc.stderr
    assert "R-0002" in proc.stderr and "R-0003" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


# --------------------------------------------------- fact alignment collisions


def collide(summary_a: str, summary_b: str) -> dict:
    """Two facts whose alignment keys are identical."""
    return {
        "facts": [
            {"type": "task", "summary": summary_a, "attrs": {}},
            {"type": "task", "summary": summary_b, "attrs": {}},
        ],
        "must_not_infer": [],
    }


LONG_A = "chase the plumber about the ensuite waste relocation before friday"
LONG_B = "chase the plumber about the ensuite waste relocation after friday"


@pytest.mark.parametrize("side", ["first", "second"])
def test_alignment_key_collision_fails_on_either_side(tmp_path, side):
    """Identical keys would silently drop a fact — refuse instead."""
    colliding = case("R-0001", gold=collide(LONG_A, LONG_B))
    clean = case("R-0001", gold=LABELLED)

    first = write_jsonl(
        tmp_path / "first.jsonl", [colliding if side == "first" else clean]
    )
    second = write_jsonl(
        tmp_path / "second.jsonl", [colliding if side == "second" else clean]
    )
    out = tmp_path / "report.md"

    proc = run(DIFF, str(first), str(second), "--out", str(out))
    assert proc.returncode == 2
    assert "R-0001" in proc.stderr
    assert "alignment key" in proc.stderr
    assert side.upper() in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_distinct_summaries_still_align_normally(tmp_path):
    """The collision guard must not reject legitimate multi-fact cases."""
    gold = {
        "facts": [
            {"type": "task", "summary": "order the flashing", "attrs": {}},
            {"type": "site_log_fact", "summary": "slab poured", "attrs": {}},
        ],
        "must_not_infer": [],
    }
    first = write_jsonl(tmp_path / "first.jsonl", [case("R-0001", gold=gold)])
    second = write_jsonl(tmp_path / "second.jsonl", [case("R-0001", gold=gold)])
    out = tmp_path / "report.md"

    proc = run(DIFF, str(first), str(second), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "identical labels: 1/1" in proc.stdout


# ------------------------------------------------------ must_not_infer shape


@pytest.mark.parametrize(
    "bad_mni", [[{"nested": "object"}], [123], "not-a-list"]
)
def test_validator_rejects_malformed_must_not_infer(tmp_path, bad_mni):
    gold = {
        "facts": [{"type": "task", "summary": "x", "attrs": {}}],
        "must_not_infer": bad_mni,
    }
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", [case("R-0001", gold=gold)])
    proc = run(VALIDATE, str(dataset))
    assert proc.returncode == 1
    assert "must_not_infer" in proc.stdout


def test_label_diff_unhashable_must_not_infer_is_controlled(tmp_path):
    """Malformed data must not surface as an unhashable-type traceback."""
    gold = {
        "facts": [{"type": "task", "summary": "x", "attrs": {}}],
        "must_not_infer": [{"nested": "object"}],
    }
    first = write_jsonl(tmp_path / "first.jsonl", [case("R-0001", gold=gold)])
    second = write_jsonl(tmp_path / "second.jsonl", [case("R-0001", gold=gold)])
    out = tmp_path / "report.md"

    proc = run(DIFF, str(first), str(second), "--out", str(out))
    assert proc.returncode == 2
    assert "must_not_infer" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "unhashable" not in proc.stderr
    assert not out.exists()


# ------------------------------------------- public fixture provenance rule
# These tests never write to a tracked repo file: the negative case uses a
# private-root `synthetic.jsonl` (also a PUBLIC class), and the positive
# case reads the shipped fixture as committed.


def test_public_fixture_rejects_founder_voice_source(tmp_path):
    """A public fixture claiming real-capture provenance is a failure.

    Either it mislabels a fabricated example, or a real capture is sitting
    in a public repository. Both need fixing, so neither may pass.
    """
    row = case("SYN-001", gold=LABELLED)
    row["meta"]["source"] = "founder_voice"
    fixture = write_jsonl(tmp_path / "synthetic.jsonl", [row])

    proc = run(VALIDATE, str(fixture), "--private-root", str(tmp_path))
    assert proc.returncode == 1
    assert "founder_voice" in proc.stdout
    assert "public synthetic fixture" in proc.stdout


def test_public_fixture_accepts_synthetic_fixture_source(tmp_path):
    row = case("SYN-001", gold=LABELLED)
    row["meta"]["source"] = "synthetic_fixture"
    fixture = write_jsonl(tmp_path / "synthetic.jsonl", [row])

    proc = run(VALIDATE, str(fixture), "--private-root", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "class: synthetic" in proc.stdout


def test_private_real_case_may_keep_founder_voice_source(tmp_path):
    """The rule is about publication, not about the value itself."""
    row = case("R-0001", gold=LABELLED)
    row["meta"]["source"] = "founder_voice"
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", [row])

    proc = run(VALIDATE, str(dataset), "--private-root", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "class: real" in proc.stdout


def test_shipped_sample_fixture_is_declared_synthetic():
    """The committed fixture must satisfy the rule it introduced."""
    fixture = REPO_ROOT / "evals" / "extraction" / "dataset.sample.jsonl"
    sources = {
        json.loads(line)["meta"]["source"]
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert sources == {"synthetic_fixture"}

    proc = run(VALIDATE, str(fixture))
    assert proc.returncode == 0, proc.stdout + proc.stderr
