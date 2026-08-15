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


ELIGIBLE_MANIFEST = {
    "schema_version": "0.2",
    "event_origin": "real",
    "creation_method": "contemporaneous_capture",
    "verbatim_capture": True,
    "ai_exposure": "none",
    "intended_use": "heldout",
}


def v2_case(cid: str, *, gold: dict | None = None, job_state: str = "confirmed",
            routing: str | None = None) -> dict:
    """A structurally valid v0.2 line (filler content, never a real capture)."""
    d = case(cid, gold=gold)
    d["context"]["job_state"] = job_state
    if job_state == "unassigned":
        d["context"]["job"] = None
    d["meta"] = {
        "modality": "voice_transcript",
        "collected_at": "2026-08-15",
        "privacy": "scrubbed",
    }
    if routing:
        d["meta"]["routing_case"] = routing
    return d


def write_manifest(tmp_path, name: str, **overrides) -> None:
    m = dict(ELIGIBLE_MANIFEST, **overrides)
    (tmp_path / name).write_text(json.dumps(m, indent=1), encoding="utf-8")


def heldout_corpus(tmp_path, n: int = 30, **manifest_overrides):
    rows = [v2_case(f"R-{i:04d}", gold=LABELLED) for i in range(1, n + 1)]
    write_jsonl(tmp_path / "dataset.heldout.jsonl", rows)
    write_manifest(tmp_path, "dataset.heldout.manifest.json", **manifest_overrides)


def gate(tmp_path):
    return run(
        VALIDATE, "--baseline-structure-ready", "--private-root", str(tmp_path)
    )


def test_twentynine_heldout_cases_do_not_pass_the_minimum_gate(tmp_path):
    heldout_corpus(tmp_path, n=29)
    proc = gate(tmp_path)
    assert proc.returncode == 1
    assert "NOT MET" in proc.stdout
    assert "29/30" in proc.stdout


def test_thirty_eligible_heldout_cases_pass_structure_only(tmp_path):
    heldout_corpus(tmp_path, n=30)
    proc = gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout

    assert "STRUCTURAL HELD-OUT GATE: MET" in out
    assert "structurally valid labels" in out
    assert "Baseline v0.1 is NOT approved" in out
    for human_fact in ("independently", "blind relabel", "frozen"):
        assert human_fact in out
    lowered = out.lower()
    assert "baseline v0.1 ready" not in lowered
    assert "baseline ready" not in lowered


def test_development_corpus_never_counts_toward_baseline(tmp_path):
    """Ruling 9: development data cannot pass as independent Baseline data."""
    rows = [v2_case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 31)]
    write_jsonl(tmp_path / "dataset.v0.jsonl", rows)
    write_manifest(
        tmp_path, "dataset.v0.manifest.json", intended_use="development"
    )
    proc = gate(tmp_path)
    assert proc.returncode == 1
    assert "NOT MET" in proc.stdout
    assert "does not exist" in proc.stdout
    assert "Development data cannot substitute" in proc.stdout


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("ai_exposure", "raw_seen", "ai_exposure"),
        ("ai_exposure", "gold_seen", "ai_exposure"),
        ("creation_method", "retrospective_reconstruction", "creation_method"),
        ("event_origin", "synthetic", "event_origin"),
        ("verbatim_capture", False, "verbatim_capture"),
    ],
)
def test_ineligible_provenance_contributes_zero(tmp_path, field, value, needle):
    heldout_corpus(tmp_path, n=30, **{field: value})
    proc = gate(tmp_path)
    assert proc.returncode == 1
    assert "eligible" in proc.stdout.lower()
    assert needle in proc.stdout


def test_legacy_corpus_without_manifest_contributes_zero(tmp_path):
    """Ruling 9: missing provenance is a hard exclusion for the gate."""
    rows = [case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 31)]
    fixed = [{**r, "meta": {**r["meta"], "held_out": True}} for r in rows]
    write_jsonl(tmp_path / "dataset.heldout.jsonl", fixed)
    proc = gate(tmp_path)
    assert proc.returncode == 1
    assert "no v0.2 corpus manifest" in proc.stdout
    assert "zero" in proc.stdout


def test_multi_job_cases_are_excluded_from_baseline_count(tmp_path):
    """30 labelled cases, but one routed multi_job -> only 29 count."""
    rows = [v2_case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 30)]
    rows.append(
        v2_case("R-0030", gold=LABELLED, job_state="unassigned",
                routing="multi_job")
    )
    write_jsonl(tmp_path / "dataset.heldout.jsonl", rows)
    write_manifest(tmp_path, "dataset.heldout.manifest.json")
    proc = gate(tmp_path)
    assert proc.returncode == 1
    assert "multi_job" in proc.stdout
    assert "29/30" in proc.stdout


# ------------------------------------------------------ v0.2 record rules


def v2_validate(tmp_path, rows, manifest_overrides=None):
    write_jsonl(tmp_path / "dataset.heldout.jsonl", rows)
    write_manifest(
        tmp_path, "dataset.heldout.manifest.json", **(manifest_overrides or {})
    )
    return run(
        VALIDATE,
        str(tmp_path / "dataset.heldout.jsonl"),
        "--private-root", str(tmp_path),
    )


def test_v2_confirmed_job_with_job_passes(tmp_path):
    proc = v2_validate(tmp_path, [v2_case("R-0001", gold=LABELLED)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "v0.2" in proc.stdout


def test_v2_confirmed_job_without_job_fails(tmp_path):
    row = v2_case("R-0001", gold=LABELLED)
    row["context"]["job"] = None
    proc = v2_validate(tmp_path, [row])
    assert proc.returncode == 1
    assert "job_state 'confirmed' requires context.job" in proc.stdout


def test_v2_unassigned_without_job_passes(tmp_path):
    proc = v2_validate(
        tmp_path, [v2_case("R-0001", gold=LABELLED, job_state="unassigned")]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_v2_unassigned_with_job_fails(tmp_path):
    row = v2_case("R-0001", gold=LABELLED, job_state="unassigned")
    row["context"]["job"] = {"name": "JOB-TEST"}
    proc = v2_validate(tmp_path, [row])
    assert proc.returncode == 1
    assert "job_state 'unassigned' requires context.job" in proc.stdout


def test_v2_suggested_job_state_is_rejected(tmp_path):
    """No third state: a suggested Job is never extractor input."""
    row = v2_case("R-0001", gold=LABELLED)
    row["context"]["job_state"] = "suggested"
    proc = v2_validate(tmp_path, [row])
    assert proc.returncode == 1
    assert "job_state" in proc.stdout


def _fact_with_category(category, ftype="site_log_fact"):
    return {
        "facts": [
            {
                "type": ftype,
                "fact_category": category,
                "summary": "structural filler",
                "attrs": {},
            }
        ],
        "must_not_infer": [],
    }


def test_v2_valid_fact_category_on_site_log_fact_passes(tmp_path):
    proc = v2_validate(
        tmp_path, [v2_case("R-0001", gold=_fact_with_category("delivery"))]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_v2_invalid_fact_category_fails(tmp_path):
    proc = v2_validate(
        tmp_path, [v2_case("R-0001", gold=_fact_with_category("issue"))]
    )
    assert proc.returncode == 1
    assert "fact_category 'issue'" in proc.stdout


def test_v2_fact_category_on_task_fails(tmp_path):
    proc = v2_validate(
        tmp_path,
        [v2_case("R-0001", gold=_fact_with_category("delivery", ftype="task"))],
    )
    assert proc.returncode == 1
    assert "only permitted on site_log_fact" in proc.stdout


def test_v2_missing_modality_fails(tmp_path):
    row = v2_case("R-0001", gold=LABELLED)
    del row["meta"]["modality"]
    proc = v2_validate(tmp_path, [row])
    assert proc.returncode == 1
    assert "meta.modality" in proc.stdout


def test_public_corpus_declaring_real_origin_fails(tmp_path):
    """event_origin: real is forbidden inside the public repository."""
    fixture = REPO_ROOT / "evals" / "extraction" / "calibration" / "synthetic.jsonl"
    manifest = (
        REPO_ROOT / "evals" / "extraction" / "calibration" / "synthetic.manifest.json"
    )
    assert not fixture.exists() and not manifest.exists()
    try:
        write_jsonl(fixture, [case("SYN-001", gold=LABELLED)])
        manifest.write_text(
            json.dumps(dict(ELIGIBLE_MANIFEST, intended_use="reference")),
            encoding="utf-8",
        )
        proc = run(VALIDATE, str(fixture))
        assert proc.returncode == 1
        assert "event_origin 'real' is forbidden in the public" in proc.stdout
    finally:
        fixture.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)


def test_path_provenance_mismatch_fails(tmp_path):
    """heldout-class file declaring development intended_use is an error."""
    heldout_corpus(tmp_path, n=1, intended_use="development")
    proc = run(
        VALIDATE,
        str(tmp_path / "dataset.heldout.jsonl"),
        "--private-root", str(tmp_path),
    )
    assert proc.returncode == 1
    assert "disagrees with the storage class" in proc.stdout


def test_historical_ai_booleans_are_rejected_as_v2_manifest(tmp_path):
    """The boolean pair maps for documentation only - never a v0.2 override."""
    rows = [v2_case("R-0001", gold=LABELLED)]
    write_jsonl(tmp_path / "dataset.heldout.jsonl", rows)
    m = dict(ELIGIBLE_MANIFEST)
    del m["ai_exposure"]
    m["ai_raw_exposed"] = True
    m["ai_gold_exposed"] = False
    (tmp_path / "dataset.heldout.manifest.json").write_text(
        json.dumps(m), encoding="utf-8"
    )
    proc = run(
        VALIDATE,
        str(tmp_path / "dataset.heldout.jsonl"),
        "--private-root", str(tmp_path),
    )
    assert proc.returncode == 1
    assert "ai_raw_exposed/ai_gold_exposed" in proc.stdout
    assert "ai_exposure" in proc.stdout


def test_legacy_v01_records_remain_structurally_valid(tmp_path):
    """v0.1 records (job present, no job_state/modality) still validate."""
    rows = [case(f"R-{i:04d}", gold=LABELLED) for i in range(1, 4)]
    dataset = write_jsonl(tmp_path / "dataset.v0.jsonl", rows)
    proc = run(VALIDATE, str(dataset), "--private-root", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "v0.1-legacy" in proc.stdout
    assert "no v0.2 corpus manifest" in proc.stdout


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
