"""Fixture tests for the development/review handoff (ADR-001 §5, ADR-003).

A gate that has never caught a planted flaw is decoration, so every test
here plants one condition and asserts the WORKFLOW TAKES THE RIGHT ACTION —
proceed, block, or stop. What is deliberately not tested is reviewer prose:
the only thing read from a review is its explicit verdict line, and anything
unrecognised is unusable. Building a scoring system over model wording would
add a second stochastic component pretending to be a gate.

Every review here is answered by an isolated fake plugin (a tiny script this
file writes), so the suite is deterministic, offline, and safe in CI. The
real plugin is exercised once, out of band, by the work package that
introduced this workflow; its raw output is archived with that run.

Run: python -m pytest tests/test_review_handoff.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.review_handoff import brief as brief_mod  # noqa: E402
from scripts.review_handoff import cli, runner, state, verdict  # noqa: E402

FIXTURES = REPO_ROOT / ".claude/skills/dev-review-handoff/fixtures"


def fixture(name: str) -> str:
    """A canned reviewer response, versioned beside the skill it exercises."""
    return (FIXTURES / name).read_text(encoding="utf-8")


APPROVE_OUTPUT = fixture("approve.txt")
ATTENTION_OUTPUT = fixture("needs-attention.txt")
NATIVE_OUTPUT = fixture("native-clean.txt")


# --------------------------------------------------------------- fixtures


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _write_fake_plugin(
    tmp_path: Path,
    *,
    outputs: list[str],
    exit_code: int = 0,
    findings_output: str | None = None,
    findings_exit_code: int = 0,
) -> Path:
    """A stand-in reviewer covering BOTH channels.

    ``outputs`` answer the adversarial (verdict-carrying) channel, one per
    round. The findings channel answers separately and is counted separately,
    so a test can assert that a round ran both.
    """
    script = tmp_path / "fake_plugin.py"
    payload = json.dumps(outputs)
    findings = json.dumps(findings_output if findings_output is not None else NATIVE_OUTPUT)
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"outputs = json.loads(r'''{payload}''')\n"
        f"findings = json.loads(r'''{findings}''')\n"
        "channel = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "stem = 'count' if channel == 'adversarial-review' else 'fcount'\n"
        "counter = Path(__file__).with_suffix('.' + stem)\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        "if channel == 'adversarial-review':\n"
        "    sys.stdout.write(outputs[min(n, len(outputs) - 1)])\n"
        f"    sys.exit({exit_code})\n"
        "sys.stdout.write(findings)\n"
        f"sys.exit({findings_exit_code})\n",
        encoding="utf-8",
    )
    return script


def _install_fake_plugin(monkeypatch, script: Path) -> None:
    monkeypatch.setenv(runner.PLUGIN_SCRIPT_ENV, str(script))
    monkeypatch.setenv(runner.PLUGIN_NODE_ENV, sys.executable)


def _plugin_calls(script: Path, *, channel: str = "adversarial-review") -> int:
    stem = ".count" if channel == "adversarial-review" else ".fcount"
    counter = script.with_suffix(stem)
    return int(counter.read_text()) if counter.exists() else 0


def _brief_text(
    *,
    commands: str = '[["python", "-c", "print(\'gate ok\')"]]',
    rounds: int = 3,
    seconds: int = 3600,
) -> str:
    return f"""
name = "sample package"
requirements = ["do the approved thing"]
acceptance_criteria = ["the approved thing is done"]
allowed_paths = ["file.txt"]
prohibitions = ["no merging", "no deploying"]
verification_commands = {commands}

[limits]
max_review_rounds = {rounds}
max_total_seconds = {seconds}
"""


@pytest.fixture
def brief_file(repo: Path):
    def _make(**kwargs) -> Path:
        path = repo / "brief.toml"
        path.write_text(_brief_text(**kwargs), encoding="utf-8")
        return path

    return _make


@pytest.fixture
def external_brief_file(tmp_path: Path):
    """A brief that lives outside the work tree, so editing it leaves the
    tree digest untouched."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)

    def _make(**kwargs) -> Path:
        path = outside / "brief.toml"
        path.write_text(_brief_text(**kwargs), encoding="utf-8")
        return path

    return _make


def _run(repo: Path, monkeypatch, argv: list[str]) -> int:
    monkeypatch.chdir(repo)
    return cli.main(argv)


def _start(repo: Path, monkeypatch, brief: Path, run_dir: Path) -> int:
    return _run(
        repo, monkeypatch, ["start", "--brief", str(brief), "--run-dir", str(run_dir)]
    )


# ------------------------------------------------------- the brief is input


def test_brief_missing_required_field_is_misuse(repo: Path, monkeypatch, tmp_path):
    """An approval that never says what is forbidden is not an approval."""
    bad = repo / "bad.toml"
    bad.write_text(
        'name = "x"\nrequirements = ["r"]\nacceptance_criteria = ["a"]\n'
        'allowed_paths = ["p"]\nverification_commands = [["true"]]\n',
        encoding="utf-8",
    )
    assert _start(repo, monkeypatch, bad, tmp_path / "run") == cli.EXIT_MISUSE
    assert not (tmp_path / "run").exists()


def test_brief_rejects_shell_string_commands(repo: Path):
    bad = repo / "bad.toml"
    bad.write_text(
        _brief_text(commands='["python -c \'print(1)\'"]'), encoding="utf-8"
    )
    with pytest.raises(brief_mod.BriefError):
        brief_mod.load_brief(bad)


def test_start_records_limits_and_archives_the_brief(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    assert _start(repo, monkeypatch, brief_file(), run_dir) == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert saved.base == _git(repo, "rev-parse", "HEAD")
    assert saved.max_review_rounds == 3
    assert (run_dir / "brief.toml").exists()
    # The archived copy is what the run is bound to, even if the source moves.
    assert "sample package" in (run_dir / "brief.toml").read_text(encoding="utf-8")


def test_start_refuses_to_restart_an_existing_run(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    brief = brief_file()
    assert _start(repo, monkeypatch, brief, run_dir) == cli.EXIT_OK
    assert _start(repo, monkeypatch, brief, run_dir) == cli.EXIT_MISUSE


# ------------------------------------------------------------- happy path


def test_pass_path_gate_then_approve_then_finish(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert saved.status == "delivered"
    assert saved.review_count == 1 and saved.auto_rounds_used == 0
    assert saved.rounds[0].verdict == "approve" and saved.rounds[0].usable
    # Every channel's raw output is archived, not just its classification.
    assert set(saved.rounds[0].raw_paths) == set(runner.REVIEW_CHANNELS)
    for path in saved.rounds[0].raw_paths.values():
        assert Path(path).exists()
    gating = Path(saved.rounds[0].raw_paths[runner.CHANNEL_VERDICT])
    assert "Verdict: approve" in gating.read_text(encoding="utf-8")
    assert json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))["status"] == (
        "delivered"
    )


def test_defect_then_fix_then_approve(repo: Path, monkeypatch, brief_file, tmp_path):
    """Round 1 reports a defect, the tree changes, round 2 approves."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[ATTENTION_OUTPUT, APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert state.load_state(run_dir).rounds[0].verdict == "needs-attention"
    # Finishing is refused while the only verdict is needs-attention.
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    (repo / "file.txt").write_text("fixed\n", encoding="utf-8")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert [r.kind for r in saved.rounds] == ["initial", "auto"]
    assert saved.auto_rounds_used == 1
    assert _plugin_calls(plugin) == 2


# ------------------------------------ a non-answer is never a pass


@pytest.mark.parametrize(
    "outputs, exit_code, expected_reason",
    [
        (["Verdict: approve"], 3, "exited"),  # reviewer crashed
        ([""], 0, "too short"),  # empty result
        (["a long review body with plenty of prose but no verdict line at all"], 0, "no recognised verdict"),
        (
            ["Verdict: approve\nsome text\nVerdict: needs-attention\n"],
            0,
            "conflicting verdicts",
        ),
    ],
)
def test_unusable_review_blocks_and_never_passes(
    repo: Path, monkeypatch, brief_file, tmp_path, outputs, exit_code, expected_reason
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=outputs, exit_code=exit_code)
    _install_fake_plugin(monkeypatch, plugin)

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert saved.rounds[-1].verdict == verdict.UNUSABLE
    assert not saved.rounds[-1].usable
    assert expected_reason in saved.rounds[-1].reason
    # It still consumed a round: a broken reviewer must not buy infinite retries.
    assert saved.review_count == 1
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_missing_plugin_is_unusable_not_a_pass(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, tmp_path / "does_not_exist.py")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert not state.load_state(run_dir).rounds[-1].usable


def test_review_timeout_is_unusable(repo: Path, monkeypatch, brief_file, tmp_path):
    slow = tmp_path / "slow_plugin.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, slow)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    exit_code = _run(
        repo, monkeypatch, ["review", "--run-dir", str(run_dir), "--timeout", "1"]
    )
    assert exit_code == cli.EXIT_BLOCKED
    last = state.load_state(run_dir).rounds[-1]
    assert not last.usable and "timed out" in last.reason


# --------------------------------------------- evidence must match the tree


def test_pass_goes_stale_when_the_tree_changes(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An approval describes the tree it read. Change the tree and it stops
    counting — this is the 'version changed' path."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).passing_round(repo) is not None

    (repo / "file.txt").write_text("changed after the review\n", encoding="utf-8")
    assert state.load_state(run_dir).passing_round(repo) is None
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_pass_goes_stale_when_head_moves(repo: Path, monkeypatch, brief_file, tmp_path):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    (repo / "file.txt").write_text("committed after the review\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later")
    assert state.load_state(run_dir).passing_round(repo) is None


def test_review_refuses_stale_verification_evidence(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    (repo / "file.txt").write_text("edited after the gate\n", encoding="utf-8")
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert state.load_state(run_dir).review_count == 0


def test_review_requires_a_verification_run_first(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


# ------------------------------------------------- a failing gate is not a pass


def test_failing_verification_blocks_the_review(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    brief = brief_file(
        commands='[["python", "-c", "print(\'ok\')"], ["python", "-c", "raise SystemExit(1)"]]'
    )
    _start(repo, monkeypatch, brief, run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _plugin_calls(plugin) == 0  # the reviewer was never called


def test_gate_commands_run_in_order_and_stop_at_the_first_failure(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Serial execution is a correctness requirement here: these suites share
    one database. The marker file records the true order."""
    marker = tmp_path / "order.txt"
    m = str(marker).replace("\\", "/")
    brief = brief_file(
        commands=(
            '[["python", "-c", "open(\'' + m + "', 'a').write('a')\"], "
            '["python", "-c", "open(\'' + m + "', 'a').write('b'); raise SystemExit(1)\"], "
            '["python", "-c", "open(\'' + m + "', 'a').write('c')\"]]"
        )
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert marker.read_text(encoding="utf-8") == "ab"  # c never ran
    gate = state.load_state(run_dir).gates[-1]
    assert gate["ran"] == 2 and gate["of"] == 3 and gate["passed"] is False


# ----------------------------------------------------------------- limits


def test_round_limit_stops_the_run(repo: Path, monkeypatch, brief_file, tmp_path):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(rounds=1), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[ATTENTION_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    for index in range(2):  # initial review + the single automatic round
        (repo / "file.txt").write_text(f"edit {index}\n", encoding="utf-8")
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
        assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    (repo / "file.txt").write_text("one more edit\n", encoding="utf-8")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert saved.review_count == 2  # the blocked attempt did not call the reviewer
    assert _plugin_calls(plugin) == 2
    assert saved.status == "stopped" and "round limit" in saved.stop_reason


def test_round_counter_survives_a_restart(repo: Path, monkeypatch, brief_file, tmp_path):
    """Resuming must not hand back spent rounds: the counter lives in the
    run directory, not in a session."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(rounds=1), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[ATTENTION_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    # A brand-new process reads the same directory.
    reloaded = state.load_state(run_dir)
    assert reloaded.review_count == 1 and reloaded.auto_rounds_left == 1
    (repo / "file.txt").write_text("after restart\n", encoding="utf-8")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).auto_rounds_left == 0


def test_time_budget_stops_the_run(repo: Path, monkeypatch, brief_file, tmp_path):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(seconds=1), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    saved = state.load_state(run_dir)
    saved.deadline_at = "2020-01-01T00:00:00+00:00"  # the deadline has passed
    saved.save(run_dir)

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _plugin_calls(plugin) == 0
    assert "time budget" in state.load_state(run_dir).stop_reason


def test_cli_cannot_raise_the_limits_the_brief_approved(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    exit_code = _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief_file(rounds=2, seconds=600)),
            "--run-dir", str(run_dir),
            "--max-review-rounds", "99",
            "--max-total-seconds", "999999",
        ],
    )
    assert exit_code == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert saved.max_review_rounds == 2 and saved.max_total_seconds == 600


# ------------------------------------------------- stopping without a pass


def test_finish_force_records_a_stop_not_a_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[ATTENTION_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    exit_code = _run(
        repo,
        monkeypatch,
        ["finish", "--run-dir", str(run_dir), "--force", "--reason", "scope exceeded"],
    )
    assert exit_code == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert saved.status == "stopped" and saved.stop_reason == "scope exceeded"
    summary = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert summary["status"] == "stopped"


def test_status_reports_a_stale_pass_as_not_current(
    repo: Path, monkeypatch, brief_file, tmp_path, capsys
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    (repo / "file.txt").write_text("moved on\n", encoding="utf-8")

    capsys.readouterr()
    assert _run(repo, monkeypatch, ["status", "--run-dir", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.split("NOTE:")[0])
    assert payload["has_current_pass"] is False
    assert payload["pass_is_stale"] is True


# -------------------------------------------------- the reviewer's inputs


def test_review_is_bound_to_the_recorded_base_and_told_the_evidence_path(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The reviewer must receive the explicit base, the approved scope, and a
    path to the raw verification output — not a summary of it."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    recorder = tmp_path / "recorder.py"
    recorder.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "channel = sys.argv[1]\n"
        "Path(sys.argv[0]).with_suffix('.' + channel + '.args')"
        ".write_text('\\n'.join(sys.argv[1:]))\n"
        "sys.stdout.write('Verdict: approve\\n\\nnothing to report at all here\\n')\n",
        encoding="utf-8",
    )
    _install_fake_plugin(monkeypatch, recorder)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    passed = recorder.with_suffix(
        "." + runner.CHANNEL_VERDICT + ".args"
    ).read_text(encoding="utf-8")
    base = state.load_state(run_dir).base
    assert f"--base {base}" in passed
    assert "--wait" in passed  # never review in the background
    assert "do the approved thing" in passed  # requirements
    assert "no merging" in passed  # prohibitions
    assert "gate-01" in passed  # path to the raw verification log


def test_build_review_argv_is_explicit_about_base_and_waiting():
    argv = runner.build_review_argv(
        channel=runner.CHANNEL_VERDICT,
        base="abc123",
        focus="F",
        plugin=Path("/p/x.mjs"),
    )
    assert argv[2] == "adversarial-review"
    assert "--base abc123" in argv[3] and "--wait" in argv[3]


# ------------------------------------------------- the verdict parse itself


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Verdict: approve", verdict.APPROVE),
        ("verdict: Approved", verdict.APPROVE),
        ("**Verdict:** needs-attention", verdict.NEEDS_ATTENTION),
        ("Verdict: changes requested", verdict.NEEDS_ATTENTION),
        ("Verdict: mostly fine probably", verdict.UNUSABLE),
        ("the reviewer approves of this change wholeheartedly", verdict.UNUSABLE),
    ],
)
def test_verdict_markers(text, expected):
    padded = text + "\n" + "x" * verdict.MIN_USEFUL_CHARS
    assert verdict.parse_verdict(padded).value == expected


def test_only_a_usable_approve_is_a_pass():
    body = "Verdict: approve\n" + "x" * verdict.MIN_USEFUL_CHARS
    assert verdict.parse_verdict(body).is_pass is True
    assert verdict.parse_verdict(body, exit_code=1).is_pass is False
    assert verdict.parse_verdict(body, timed_out=True).is_pass is False
    assert verdict.parse_verdict(None).is_pass is False
    assert verdict.parse_verdict("Verdict: needs-attention" + "x" * 40).is_pass is False


# ------------------------------------------- both channels, and the base


def test_a_round_runs_both_review_channels(repo: Path, monkeypatch, brief_file, tmp_path):
    """One channel silently loses findings: in the package that motivated
    this workflow the native channel was clean in rounds where the
    adversarial channel found real defects, and once found a defect the
    adversarial channel never saw."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _plugin_calls(plugin, channel=runner.CHANNEL_VERDICT) == 1
    assert _plugin_calls(plugin, channel=runner.CHANNEL_FINDINGS) == 1

    summary = json.loads(
        (run_dir / "delivery.json").read_text(encoding="utf-8")
        if (run_dir / "delivery.json").exists()
        else "{}"
    )
    assert summary == {} or summary["archived_review_artefacts"] == 2


def test_findings_channel_failure_makes_the_round_unusable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An approve from one channel while the other never ran is an
    incomplete review, and an incomplete review is not a pass."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(
        tmp_path, outputs=[APPROVE_OUTPUT], findings_exit_code=4
    )
    _install_fake_plugin(monkeypatch, plugin)

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    last = state.load_state(run_dir).rounds[-1]
    assert last.verdict == verdict.UNUSABLE
    assert "findings channel" in last.reason
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_native_channel_receives_no_focus_text():
    """The findings channel takes no focus argument; sending one would be
    silently ignored or mis-parsed by the plugin."""
    argv = runner.build_review_argv(
        channel=runner.CHANNEL_FINDINGS, base="abc", focus="LONG FOCUS TEXT",
        plugin=Path("/p/x.mjs"),
    )
    assert "LONG FOCUS TEXT" not in argv[3]
    assert "--base abc" in argv[3] and "--wait" in argv[3]


def test_base_is_pinned_to_the_merge_base_not_to_head(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Re-basing each round onto the previous head is how a package reaches
    the end without anything having reviewed it as a whole."""
    merge_point = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "integration")
    (repo / "file.txt").write_text("package work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "package commit")
    assert _git(repo, "rev-parse", "HEAD") != merge_point

    run_dir = tmp_path / "run"
    exit_code = _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief_file()),
            "--run-dir", str(run_dir),
            "--integration-ref", "integration",
        ],
    )
    assert exit_code == cli.EXIT_OK
    assert state.load_state(run_dir).base == merge_point


def test_explicit_base_that_is_not_the_merge_base_is_refused(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    _git(repo, "branch", "integration")
    (repo / "file.txt").write_text("package work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "package commit")
    head = _git(repo, "rev-parse", "HEAD")

    exit_code = _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief_file()),
            "--run-dir", str(tmp_path / "run"),
            "--integration-ref", "integration",
            "--base", head,
        ],
    )
    assert exit_code == cli.EXIT_MISUSE


def test_stop_records_the_reason_without_a_pass(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Stopping and escalating is a first-class outcome: it is what the
    manual loop did with the one finding that needed authorisation nobody
    had yet."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    exit_code = _run(
        repo,
        monkeypatch,
        ["stop", "--run-dir", str(run_dir), "--reason", "needs scope the brief does not grant"],
    )
    assert exit_code == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert saved.status == "stopped"
    assert saved.stop_reason == "needs scope the brief does not grant"


def test_delivery_records_measurable_cost(repo: Path, monkeypatch, brief_file, tmp_path):
    """Founder attention per verified change is the metric this workflow is
    judged by, so the run records its own cost from the first run."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)])

    summary = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert summary["archived_review_artefacts"] == summary["expected_review_artefacts"] == 2
    assert summary["wall_clock_seconds"] >= 0
    # Durations are rounded to a tenth, so a fast command legitimately
    # records 0.0; what must hold is that the fields exist and are numbers.
    assert isinstance(summary["gate_seconds"], (int, float))
    assert isinstance(summary["review_seconds"], (int, float))


@pytest.mark.parametrize(
    "name, expected",
    [
        ("approve.txt", verdict.APPROVE),
        ("needs-attention.txt", verdict.NEEDS_ATTENTION),
        ("no-verdict.txt", verdict.UNUSABLE),
        ("conflicting.txt", verdict.UNUSABLE),
        ("empty.txt", verdict.UNUSABLE),
        ("native-clean.txt", verdict.UNUSABLE),
    ],
)
def test_every_fixture_maps_to_the_documented_action(name, expected):
    """The fixture table in the skill directory is the contract; this keeps
    the table and the code from drifting apart."""
    assert verdict.parse_verdict(fixture(name)).value == expected


def test_a_brief_saying_python_uses_this_interpreter():
    """Caught by running this workflow on itself: a bare "python" resolved to
    the system interpreter, which has none of the project's tooling, and the
    failure looked like a broken gate rather than a misresolved command."""
    assert runner.resolve_argv(["python", "-m", "pytest"]) == [
        sys.executable,
        "-m",
        "pytest",
    ]
    assert runner.resolve_argv(["python3", "-c", "x"])[0] == sys.executable
    # Anything else is left exactly as the brief wrote it.
    assert runner.resolve_argv(["ruff", "check", "."]) == ["ruff", "check", "."]
    assert runner.resolve_argv([]) == []


def test_gate_runs_the_resolved_interpreter(repo: Path, monkeypatch, brief_file, tmp_path):
    marker = str(tmp_path / "interp.txt").replace("\\", "/")
    brief = brief_file(
        commands=(
            '[["python", "-c", "import sys; open(\'' + marker
            + "', 'w').write(sys.executable)\"]]"
        )
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert Path(marker).read_text(encoding="utf-8") == sys.executable


# ------------------------------------------------------------------------
# Regressions for the five defects the REAL reviewer found when this
# workflow was first run against itself (run .claude/handoff/self-v1,
# review 01). Each test fails against the code as it stood then.
# ------------------------------------------------------------------------


def test_an_empty_findings_channel_is_not_a_completed_review(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Exiting 0 with nothing to say is not a review. Before the fix the
    round became a usable approve and could deliver."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(
        tmp_path, outputs=[APPROVE_OUTPUT], findings_output=""
    )
    _install_fake_plugin(monkeypatch, plugin)

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    last = state.load_state(run_dir).rounds[-1]
    assert last.verdict == verdict.UNUSABLE
    assert "no usable output" in last.reason
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_stop_stays_a_stop_even_after_an_approval(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A run stopped for a conflict or for scope it was not granted must
    record exactly that. Before the fix an earlier approval turned the stop
    into a delivery and discarded the reason."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).passing_round(repo) is not None

    exit_code = _run(
        repo,
        monkeypatch,
        ["stop", "--run-dir", str(run_dir), "--reason", "requirements conflict"],
    )
    assert exit_code == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert saved.status == "stopped"
    assert saved.stop_reason == "requirements conflict"
    summary = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert summary["status"] == "stopped"


def test_editing_the_brief_after_approval_blocks_the_run(
    repo: Path, monkeypatch, external_brief_file, tmp_path
):
    """The run executes the archived brief, and refuses to continue once the
    source no longer matches it — otherwise the approved verification
    commands or scope could be swapped underneath an approved run."""
    run_dir = tmp_path / "run"
    source = external_brief_file()
    _start(repo, monkeypatch, source, run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    source.write_text(
        _brief_text(commands='[["python", "-c", "print(\'weakened\')"]]'),
        encoding="utf-8",
    )
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_tampering_with_the_archived_brief_blocks_the_run(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    archived = run_dir / "brief.toml"
    archived.write_text(
        _brief_text(commands='[["python", "-c", "print(\'swapped\')"]]'),
        encoding="utf-8",
    )
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_failing_gate_after_approval_blocks_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A delivery may not sit on top of a failing verification run, even one
    that failed after the approval (a flaky test, a broken dependency)."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).passing_round(repo) is not None

    # The same tree, verified again, now fails.
    saved = state.load_state(run_dir)
    failed = dict(saved.gates[-1])
    failed["passed"] = False
    saved.gates.append(failed)
    saved.save(run_dir)

    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert state.load_state(run_dir).status == "open"


def test_delivery_requires_the_gate_to_match_the_reviewed_inputs(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    stale_gate = dict(saved.gates[-1])
    stale_gate["tree_digest"] = "a-different-tree"
    saved.gates.append(stale_gate)
    saved.save(run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_gate_commands_stop_when_the_budget_runs_out_mid_suite(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Checking the budget only before the first command would let a run with
    seconds left execute hours of tests."""
    run_dir = tmp_path / "run"
    marker = str(tmp_path / "ran.txt").replace("\\", "/")
    # The first command asks for far more time than the whole run has, so the
    # budget is certainly gone when it returns. Sizing it against the elapsed
    # time of the surrounding steps would make this test a coin flip.
    brief = brief_file(
        commands=(
            '[["python", "-c", "import time; time.sleep(30)"], '
            '["python", "-c", "open(\'' + marker + "', 'w').write('second ran')\"]]"
        ),
        seconds=2,
    )
    _start(repo, monkeypatch, brief, run_dir)
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert not Path(marker).exists()
    gate = state.load_state(run_dir).gates[-1]
    assert gate["passed"] is False
    assert gate["commands"][-1]["timed_out"] is True


def test_an_approval_produced_after_the_deadline_cannot_deliver(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    saved.deadline_at = "2020-01-01T00:00:00+00:00"
    saved.save(run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_an_older_state_schema_is_refused_clearly(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Adding a stored field without bumping the version made an older run
    die with a TypeError deep in the loader; it must report cleanly instead.
    Found by re-running this workflow on itself across that change."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    payload["schema_version"] = state.SCHEMA_VERSION - 1
    (run_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError) as info:
        state.load_state(run_dir)
    assert "not supported" in str(info.value)
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_MISUSE


def test_a_corrupt_state_file_is_refused_clearly(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    del payload["base"]
    (run_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError):
        state.load_state(run_dir)


# ------------------------------------------------------------------------
# Regressions for the four defects the real reviewer found on the second
# round (run .claude/handoff/self-v2, review 01).
# ------------------------------------------------------------------------


def test_a_stopped_run_cannot_later_deliver(repo: Path, monkeypatch, brief_file, tmp_path):
    """Stopping records "this needs the founder". A later finish must not
    convert it into a delivery using the approval that predates the stop."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _run(
        repo,
        monkeypatch,
        ["stop", "--run-dir", str(run_dir), "--reason", "requirements conflict"],
    )

    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert saved.status == "stopped"
    assert saved.stop_reason == "requirements conflict"


def test_a_stopped_run_cannot_be_gated_or_reviewed_again(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)
    _run(repo, monkeypatch, ["stop", "--run-dir", str(run_dir), "--reason", "out of scope"])

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _plugin_calls(plugin) == 0


def test_a_delivered_run_is_terminal_too(repo: Path, monkeypatch, brief_file, tmp_path):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_delivery_revalidates_the_approved_inputs(
    repo: Path, monkeypatch, external_brief_file, tmp_path
):
    """gate and review check the brief; finish must too, or a brief edited
    after the last review delivers under requirements nobody approved.

    The brief is deliberately outside the work tree: editing it then leaves
    the tree digest unchanged, so the staleness check cannot notice and only
    the explicit input check can."""
    run_dir = tmp_path / "run"
    source = external_brief_file()
    _start(repo, monkeypatch, source, run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    source.write_text(
        _brief_text(commands='[["python", "-c", "print(\'weakened\')"]]'),
        encoding="utf-8",
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert state.load_state(run_dir).status == "open"


def test_a_tree_edited_during_verification_does_not_count_as_verified(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A file edited after its own tests passed but before the suite finished
    would otherwise have those passes recorded against the edited tree."""
    target = repo / "file.txt"
    brief = brief_file(
        commands=(
            '[["python", "-c", "open(r\'' + str(target).replace("\\", "/")
            + "', 'w').write('edited mid-suite')\"]]"
        )
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)
    plugin = _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT])
    _install_fake_plugin(monkeypatch, plugin)

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    gate = state.load_state(run_dir).gates[-1]
    assert gate["inputs_stable"] is False
    assert gate["passed"] is False
    # Every command exited 0 — it is the moving tree that fails the gate.
    assert all(c["exit_code"] == 0 for c in gate["commands"])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert _plugin_calls(plugin) == 0


def test_an_interrupted_review_keeps_its_round_and_its_evidence(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A session that dies between the two channels must not resume with the
    round unspent, nor overwrite the interrupted attempt's raw output."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(rounds=1), run_dir)

    # A reviewer that answers the first channel and then kills the process
    # outright, which is what an interrupted session looks like from here.
    crasher = tmp_path / "crasher.py"
    crasher.write_text(
        "import os, sys\n"
        "if sys.argv[1] == 'adversarial-review':\n"
        "    sys.stdout.write('Verdict: approve\\n\\n' + 'x' * 80 + '\\n')\n"
        "    sys.exit(0)\n"
        "os._exit(9)\n",
        encoding="utf-8",
    )
    _install_fake_plugin(monkeypatch, crasher)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert saved.review_count == 1  # the attempt was counted
    assert not saved.rounds[-1].usable
    first_evidence = Path(saved.rounds[-1].raw_paths[runner.CHANNEL_VERDICT])
    assert first_evidence.exists()

    # A second attempt claims new filenames rather than trampling the first.
    (repo / "file.txt").write_text("after the interruption\n", encoding="utf-8")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    saved = state.load_state(run_dir)
    assert saved.review_count == 2
    assert saved.rounds[1].raw_paths[runner.CHANNEL_VERDICT] != str(first_evidence)
    assert first_evidence.exists()


def test_a_round_is_counted_before_the_reviewer_is_called(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The counter is persisted first, so a process that dies mid-round
    cannot buy the round back on resume."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    seen = {}

    def spy(**kwargs):
        seen["count_during_call"] = state.load_state(run_dir).review_count
        raise RuntimeError("stop here")

    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    monkeypatch.setattr(cli, "invoke_review_round", spy)
    with pytest.raises(RuntimeError):
        _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert seen["count_during_call"] == 1
