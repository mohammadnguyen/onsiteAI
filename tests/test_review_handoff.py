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
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.review_handoff import brief as brief_mod  # noqa: E402
from scripts.review_handoff import cli, findings, locking, prompt, runner  # noqa: E402
from scripts.review_handoff import state, verdict  # noqa: E402

FIXTURES = REPO_ROOT / ".claude/skills/dev-review-handoff/fixtures"


def fixture(name: str) -> str:
    """A canned reviewer response, versioned beside the skill it exercises."""
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------- fixtures


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str = "work") -> str:
    """Commit everything in the work tree.

    `review` refuses a dirty tree, because the reviewer is handed the commit
    range base..HEAD and would never see uncommitted work. Tests that change
    the package therefore commit, exactly as a real run must.
    """
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def isolated_locks(tmp_path: Path, monkeypatch):
    """Keep the machine-wide gate lock out of the real shared location.

    `gate` takes a lock named by the brief in the OS temp directory, which is
    shared with every real handoff run on the machine. Without this fixture
    the suite contends with real runs and with itself, and a gate that loses
    the race waits 45 minutes rather than failing.
    """
    monkeypatch.setenv(locking.LOCK_DIR_ENV, str(tmp_path / "locks"))


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
    lock_name: str | None = None,
) -> str:
    lock_line = f'shared_lock = "{lock_name}"\n' if lock_name else ""
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
{lock_line}"""


@pytest.fixture
def brief_file(repo: Path):
    def _make(**kwargs) -> Path:
        path = repo / "brief.toml"
        path.write_text(_brief_text(**kwargs), encoding="utf-8")
        # Committed: an uncommitted brief is uncommitted work like any other,
        # and `review` refuses a dirty tree.
        _commit(repo, "brief")
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


def _payload(
    verdict_word: str,
    *,
    findings_list: list[dict] | None = None,
    summary: str = "the change was read in full against the brief",
    text: str | None = None,
) -> str:
    """The shape the real plugin prints under --json.

    The adversarial channel runs against the plugin's own JSON output schema
    (schemas/review-output.schema.json), and --json wraps that object in the
    companion payload. Tests that exercise the structured path must use the
    real shape, or they would be pinning an interface nobody implements.
    """
    result = {
        "verdict": verdict_word,
        "summary": summary,
        "findings": findings_list or [],
        "next_steps": [],
    }
    return json.dumps(
        {
            "review": "Adversarial Review",
            "target": {"label": "branch"},
            "codex": {"status": 0, "stdout": text or json.dumps(result), "stderr": ""},
            "result": result,
            "rawOutput": json.dumps(result),
            "parseError": None,
        }
    )


def _structured_finding(
    *,
    severity: str = "high",
    title: str = "unchecked index",
    file: str = "file.txt",
    line: int = 1,
) -> dict:
    return {
        "severity": severity,
        "title": title,
        "body": "the loop reads one past the end when the list is empty",
        "file": file,
        "line_start": line,
        "line_end": line,
        "confidence": 0.9,
        "recommendation": "guard the empty case",
    }


def _native_payload(text: str) -> str:
    """The shape the plugin really prints for the NATIVE channel under --json.

    Note what is NOT in it: no `result`, no `rawOutput`. The review prose
    lives only in codex.stdout, and the plugin leaves that empty when a turn
    completes without ever producing review text.
    """
    return json.dumps(
        {
            "review": "Review",
            "target": {"mode": "branch", "label": "branch diff against abc"},
            "threadId": "t-1",
            "sourceThreadId": "t-1",
            "codex": {"status": 0, "stderr": "", "stdout": text, "reasoning": []},
        }
    )


# The only shape a reviewer answer can legitimately take: every channel is
# invoked with --json, so every channel owes a JSON envelope. Tests that use
# anything else are testing a refusal.
APPROVE_OUTPUT = _payload("approve")
ATTENTION_OUTPUT = _payload("needs-attention")
NATIVE_OUTPUT = _native_payload(
    "I read the whole range and the archived verification output. Nothing "
    "material to report on this head."
)


def _run(repo: Path, monkeypatch, argv: list[str]) -> int:
    monkeypatch.chdir(repo)
    return cli.main(argv)


def _triage_all(repo: Path, monkeypatch, run_dir: Path) -> None:
    """Attest that every prose channel of every usable round was read.

    The release condition requires this explicitly; tests that are about
    something else say so here in one line rather than repeating it. The
    requirement itself has its own tests.
    """
    saved = state.load_state(run_dir)
    done = {(int(a["round"]), a["channel"]) for a in saved.triage}
    done |= {(f["round"], f["channel"]) for f in saved.findings}
    for record in saved.rounds:
        if not record.usable:
            continue
        for channel in runner.REVIEW_CHANNELS:
            if (record.number, channel) in done:
                continue
            _run(
                repo,
                monkeypatch,
                [
                    "findings", "none",
                    "--run-dir", str(run_dir),
                    "--round", str(record.number),
                    "--channel", channel,
                    "--note", "read the archived output; nothing to report",
                ],
            )


def _start(repo: Path, monkeypatch, brief: Path, run_dir: Path) -> int:
    # The throwaway repository has no remote, so the integration reference is
    # its own branch. An unresolvable reference is a MISUSE by design and is
    # covered by its own test.
    return _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief),
            "--run-dir", str(run_dir),
            "--integration-ref", "main",
        ],
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
    _triage_all(repo, monkeypatch, run_dir)
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
    _commit(repo, "fix the reported defect")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    _triage_all(repo, monkeypatch, run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert [r.kind for r in saved.rounds] == ["initial", "auto"]
    assert saved.auto_rounds_used == 1
    assert _plugin_calls(plugin) == 2


# ------------------------------------ a non-answer is never a pass


@pytest.mark.parametrize(
    "outputs, exit_code, expected_reason",
    [
        ([_payload("approve")], 3, "exited"),  # reviewer crashed
        ([""], 0, "not a JSON envelope"),  # nothing at all
        (
            ["a long review body with plenty of prose but no verdict line at all"],
            0,
            "not a JSON envelope",
        ),
        (
            ["Verdict: approve\nsome text\nVerdict: needs-attention\n"],
            0,
            "not a JSON envelope",
        ),
        ([json.dumps({"review": "x", "codex": {"status": 0, "stdout": "", "stderr": ""}})],
         0, "does not match the plugin's protocol"),  # envelope, no result
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
        _commit(repo, f"edit {index}")
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
        assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    (repo / "file.txt").write_text("one more edit\n", encoding="utf-8")
    _commit(repo, "one more edit")
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
    _commit(repo, "after restart")
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
            "--integration-ref", "main",
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
    ).read_text(encoding="utf-8").split("\n")
    base = state.load_state(run_dir).base
    assert passed[passed.index("--base") + 1] == base
    # Inert in this plugin version (reviews always run in the foreground),
    # but it is the requirement this workflow depends on, so it is sent.
    assert "--wait" in passed
    focus = passed[-1]
    assert "do the approved thing" in focus  # requirements
    assert "no merging" in focus  # prohibitions
    assert "gate-01" in focus  # path to the raw verification log


def test_build_review_argv_is_explicit_about_base_and_waiting():
    argv = runner.build_review_argv(
        channel=runner.CHANNEL_VERDICT,
        base="abc123",
        focus="F",
        plugin=Path("/p/x.mjs"),
    )
    assert argv[2] == "adversarial-review"
    # Separate elements, never one packed string: the plugin re-splits a
    # single raw argument with a shell-like tokeniser.
    assert argv[argv.index("--base") + 1] == "abc123"
    assert "--wait" in argv and "--json" in argv
    assert argv[-2:] == ["--", "F"]


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
    assert "LONG FOCUS TEXT" not in argv
    assert "--" not in argv
    assert argv[argv.index("--base") + 1] == "abc"
    assert "--wait" in argv


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
    _triage_all(repo, monkeypatch, run_dir)
    _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)])

    summary = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
    assert summary["archived_review_artefacts"] == summary["expected_review_artefacts"] == 2
    assert summary["wall_clock_seconds"] >= 0
    # Durations are rounded to a tenth, so a fast command legitimately
    # records 0.0; what must hold is that the fields exist and are numbers.
    assert isinstance(summary["gate_seconds"], (int, float))
    assert isinstance(summary["review_seconds"], (int, float))


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
        tmp_path, outputs=[APPROVE_OUTPUT], findings_output=_native_payload("")
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
    _triage_all(repo, monkeypatch, run_dir)
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
    _commit(repo, "after the interruption")
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


# ------------------------------------------------------------------------
# Regressions for the three defects found on the third round
# (.claude/handoff/self-v2, review 02).
# ------------------------------------------------------------------------


def test_a_second_binary_edit_invalidates_the_approval(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A second edit to a tracked binary must invalidate the approval.

    Raised as a defect on the ground that "Binary files ... differ" is the
    same string however the bytes change. Checked and refuted: the `index`
    line above it carries the post-image blob hash, so the diffs differ. The
    test stays because the PROPERTY is what matters, not the mechanism that
    happens to provide it."""
    binary = repo / "asset.bin"
    binary.write_bytes(b"\x00original\xff")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add a binary")

    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))

    binary.write_bytes(b"\x00first-change\xff")
    _commit(repo, "first binary change")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    assert state.load_state(run_dir).passing_round(repo) is not None

    # Uncommitted, deliberately: this is the window in which an approval
    # could still be claimed for bytes nobody reviewed.
    binary.write_bytes(b"\x00second-change-entirely-different\xff")
    assert state.load_state(run_dir).passing_round(repo) is None
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_an_unresolvable_integration_ref_does_not_fall_back_to_head(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Falling back to HEAD would bind the review to a base that covers none
    of the package's own commits."""
    (repo / "file.txt").write_text("package work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "package commit")

    exit_code = _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief_file()),
            "--run-dir", str(tmp_path / "run"),
            "--integration-ref", "origin/does-not-exist",
        ],
    )
    assert exit_code == cli.EXIT_MISUSE
    assert not (tmp_path / "run" / "run.json").exists()


def test_an_unresolvable_integration_ref_accepts_a_checked_explicit_base(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "file.txt").write_text("package work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "package commit")

    run_dir = tmp_path / "run"
    exit_code = _run(
        repo,
        monkeypatch,
        [
            "start",
            "--brief", str(brief_file()),
            "--run-dir", str(run_dir),
            "--integration-ref", "origin/does-not-exist",
            "--base", first,
        ],
    )
    assert exit_code == cli.EXIT_OK
    assert state.load_state(run_dir).base == first


def test_every_gate_attempt_keeps_its_own_logs(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A re-run before the next review used to overwrite the previous
    attempt's logs, so a failed attempt could vanish while its record still
    pointed at the replacement output. Observed in this workflow's own run."""
    failing = repo / "trip.txt"
    brief = brief_file(
        commands=(
            '[["python", "-c", "import os,sys; sys.exit(1 if os.path.exists(r\''
            + str(failing).replace("\\", "/")
            + "') else 0)\"]]"
        )
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)

    failing.write_text("fail now\n", encoding="utf-8")
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    failing.unlink()
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert len(saved.gates) == 2
    first_log = Path(saved.gates[0]["commands"][0]["log"])
    second_log = Path(saved.gates[1]["commands"][0]["log"])
    assert first_log != second_log
    assert first_log.exists() and second_log.exists()
    assert "exit: 1" in first_log.read_text(encoding="utf-8")
    assert "exit: 0" in second_log.read_text(encoding="utf-8")


# ------------------------------------------------------------------------
# Regressions for the two defects found on the fourth round
# (.claude/handoff/self-v2, review 03).
# ------------------------------------------------------------------------


def test_editing_an_untracked_non_ascii_file_invalidates_the_approval(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """git quotes and escapes non-ASCII paths unless asked not to, and the
    escaped name does not exist on disk. Reading it failed, a constant was
    hashed instead, and every later edit to that file left the digest
    unchanged.

    Exercised after the approval, which is where it bites: an untracked file
    cannot exist at review time any more (the run refuses a dirty tree), but
    one appearing or changing afterwards must still invalidate the approval
    that was bound to the earlier tree.
    """
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    assert state.load_state(run_dir).passing_round(repo) is not None

    exotic = repo / "caf\u00e9-\u6e2c\u8a66.txt"
    exotic.write_text("first contents\n", encoding="utf-8")
    first = state.tree_digest(repo)
    assert state.load_state(run_dir).passing_round(repo) is None
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    # The defect proper: the CONTENT of such a file must reach the digest.
    # It used to hash a constant, so every later edit left it unchanged.
    exotic.write_text("entirely different contents\n", encoding="utf-8")
    assert state.tree_digest(repo) != first


def test_an_unreadable_untracked_file_fails_closed(repo: Path, monkeypatch):
    """A file that cannot be read cannot be shown to be unchanged, so the
    fingerprint refuses rather than hashing a placeholder."""
    (repo / "unreadable.bin").write_bytes(b"x")
    real_read = Path.read_bytes

    def boom(self):
        if self.name == "unreadable.bin":
            raise OSError(13, "Permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    with pytest.raises(state.StateError) as info:
        state.tree_digest(repo)
    assert "cannot read untracked file" in str(info.value)


def test_an_interrupted_gate_is_recorded_incomplete_and_blocks_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Recording a gate only on completion meant an interrupted re-run left
    an older passing gate as the newest record, so a delivery could sit on
    verification that never finished."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[APPROVE_OUTPUT]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).passing_round(repo) is not None

    # A second verification that dies part-way through.
    def die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_gate_commands", die)
    with pytest.raises(KeyboardInterrupt):
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    assert len(saved.gates) == 2
    assert saved.gates[-1]["passed"] is False
    assert "never completed" in saved.gates[-1]["note"]
    monkeypatch.undo()
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_retry_after_an_interrupted_gate_keeps_both_directories(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)

    def die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_gate_commands", die)
    with pytest.raises(KeyboardInterrupt):
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    monkeypatch.undo()

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    saved = state.load_state(run_dir)
    assert len(saved.gates) == 2
    assert saved.gates[0]["passed"] is False and saved.gates[1]["passed"] is True
    # The retry claimed its own directory rather than the interrupted one.
    assert Path(saved.gates[1]["commands"][0]["log"]).parent.name == "gate-02"


# =======================================================================
# Supplementary correction round: the six reported defects, the release
# condition, and the reviewer instructions.
#
# Every test here plants the real failure and asserts the workflow's
# ACTION, same as the rest of the file.
# =======================================================================


def _argv_recorder(tmp_path: Path) -> Path:
    """A fake plugin that records its argv exactly as the OS delivered it."""
    script = tmp_path / "argv_recorder.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "channel = sys.argv[1]\n"
        "Path(sys.argv[0]).with_suffix('.' + channel + '.json').write_text(\n"
        "    json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "sys.stdout.write('Verdict: approve\\n\\nnothing to report at all here\\n')\n",
        encoding="utf-8",
    )
    return script


HOSTILE_FOCUS = (
    "evidence at D:\\runs\\gate-01\\caf\u00e9 log.txt; the reviewer's own words; "
    "--base pretend-flag; \u4e2d\u6587\u6e2c\u8a66"
)


def test_focus_text_reaches_the_reviewer_byte_for_byte(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The defect this replaces: all flags and the prose were packed into ONE
    argument, and the plugin re-splits a single raw argument with a shell-like
    tokeniser (normalizeArgv -> splitRawArgumentString). That ate the
    backslashes out of Windows paths, removed apostrophes, and could read
    flag-like text inside the brief as an option."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    recorder = _argv_recorder(tmp_path)
    _install_fake_plugin(monkeypatch, recorder)
    focus_file = tmp_path / "focus.txt"
    focus_file.write_text(HOSTILE_FOCUS, encoding="utf-8")

    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(
        repo,
        monkeypatch,
        ["review", "--run-dir", str(run_dir), "--focus-file", str(focus_file)],
    )

    argv = json.loads(
        recorder.with_suffix("." + runner.CHANNEL_VERDICT + ".json").read_text(
            encoding="utf-8"
        )
    )
    # One element per flag, one element for the whole prose, after "--".
    assert argv[argv.index("--base") + 1] == state.load_state(run_dir).base
    assert argv[-2] == "--"
    delivered = argv[-1]
    assert HOSTILE_FOCUS in delivered
    assert "D:\\runs\\gate-01\\caf\u00e9 log.txt" in delivered
    assert "reviewer's own words" in delivered
    assert "\u4e2d\u6587\u6e2c\u8a66" in delivered


REAL_PLUGIN_ARGS = runner.DEFAULT_PLUGIN_SCRIPT.parent / "lib" / "args.mjs"


@pytest.mark.skipif(
    shutil.which("node") is None or not REAL_PLUGIN_ARGS.exists(),
    reason="the installed review plugin and node are needed to check its real parser",
)
def test_the_installed_plugins_own_parser_returns_the_focus_text_unchanged(tmp_path: Path):
    """Not a fake: this runs the INSTALLED plugin's argument parser over the
    argv we build, with the same option table its review command uses, and
    checks what the reviewer would actually receive."""
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        "const mod = await import(process.argv[2]);\n"
        "const argv = JSON.parse(process.argv[3]);\n"
        "const normalized = argv.length === 1\n"
        "  ? mod.splitRawArgumentString(argv[0])\n"
        "  : argv;\n"
        "const parsed = mod.parseArgs(normalized, {\n"
        "  valueOptions: ['base', 'scope', 'model', 'cwd'],\n"
        "  booleanOptions: ['json', 'background', 'wait'],\n"
        "  aliasMap: { m: 'model', C: 'cwd' }\n"
        "});\n"
        "console.log(JSON.stringify({ options: parsed.options,"
        " focus: parsed.positionals.join(' ') }));\n",
        encoding="utf-8",
    )
    argv = runner.build_review_argv(
        channel=runner.CHANNEL_VERDICT,
        base="0123456789abcdef0123456789abcdef01234567",
        focus=HOSTILE_FOCUS,
        plugin=Path("plugin.mjs"),
    )
    proc = subprocess.run(
        [
            "node",
            str(probe),
            REAL_PLUGIN_ARGS.resolve().as_uri(),
            json.dumps(argv[3:]),  # what the plugin sees after the subcommand
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    parsed = json.loads(proc.stdout)
    assert parsed["focus"] == HOSTILE_FOCUS
    assert parsed["options"]["base"] == "0123456789abcdef0123456789abcdef01234567"
    assert parsed["options"]["wait"] is True
    assert parsed["options"]["scope"] == "branch"


def test_the_old_single_string_form_is_what_corrupted_the_text(tmp_path: Path):
    """The counter-example, kept so the fix cannot be quietly undone: fed as
    ONE string, the same parser destroys the path and the apostrophe."""
    if shutil.which("node") is None or not REAL_PLUGIN_ARGS.exists():
        pytest.skip("the installed review plugin and node are needed")
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        "const mod = await import(process.argv[2]);\n"
        "console.log(JSON.stringify(mod.splitRawArgumentString(process.argv[3])));\n",
        encoding="utf-8",
    )
    packed = f"--wait --base abc --scope branch {HOSTILE_FOCUS}"
    proc = subprocess.run(
        ["node", str(probe), REAL_PLUGIN_ARGS.resolve().as_uri(), packed],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rebuilt = " ".join(json.loads(proc.stdout))
    assert "D:\\runs\\gate-01\\caf\u00e9 log.txt" not in rebuilt  # backslashes eaten
    assert HOSTILE_FOCUS not in rebuilt


# ------------------------------------------------------- concurrency


def test_a_second_command_cannot_write_a_run_another_one_holds(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    held = locking.FileLock(locking.run_lock_path(run_dir), purpose="test")
    held.acquire()
    try:
        assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
        assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
        assert (
            _run(repo, monkeypatch, ["stop", "--run-dir", str(run_dir), "--reason", "x"])
            == cli.EXIT_BLOCKED
        )
        assert state.load_state(run_dir).status == "open"
    finally:
        held.release()
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_stop_during_a_review_is_refused_rather_than_silently_overwritten(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The defect: a stop landing mid-review was undone when the review saved
    the state it had loaded minutes earlier. The stop must be visibly refused,
    not lost."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    seen = {}

    def fake_round(**kwargs):
        seen["stop"] = _run(
            repo,
            monkeypatch,
            ["stop", "--run-dir", str(run_dir), "--reason", "concurrent stop"],
        )
        results = {}
        for channel, path in kwargs["raw_paths"].items():
            text = _payload("approve") if channel == runner.CHANNEL_VERDICT else NATIVE_OUTPUT
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            results[channel] = runner.CommandResult(
                argv=["fake"],
                exit_code=0,
                stdout=text,
                stderr="",
                duration_seconds=0.1,
                timed_out=False,
                log_path=path,
            )
        return results

    monkeypatch.setattr(cli, "invoke_review_round", fake_round)
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert seen["stop"] == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert saved.status == "open"  # the stop never half-applied
    assert saved.review_count == 1


def test_breaking_a_lock_is_explicit_and_recorded(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A crashed session leaves a lock. Breaking it never touches the holder
    process, and the break is written into the run so it is not invisible."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    locking.FileLock(locking.run_lock_path(run_dir), purpose="crashed").acquire()
    assert _run(repo, monkeypatch, ["status", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert (
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir), "--break-lock"])
        == cli.EXIT_OK
    )
    events = state.load_state(run_dir).events
    assert any(e["event"] == "run lock broken" for e in events)


def test_two_runs_sharing_one_database_do_not_verify_at_the_same_time(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Different runs have different run directories, so the per-run lock does
    not keep them off the one PostgreSQL instance these suites share.
    Concurrency there has already produced phantom failures here."""
    monkeypatch.setenv(locking.LOCK_DIR_ENV, str(tmp_path / "locks"))
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    other = locking.FileLock(
        locking.shared_lock_path(locking.DEFAULT_SHARED_LOCK_NAME),
        purpose="gate",
        run_id="some-other-run",
    )
    other.acquire()
    try:
        assert (
            _run(
                repo,
                monkeypatch,
                ["gate", "--run-dir", str(run_dir), "--shared-lock-wait", "0"],
            )
            == cli.EXIT_BLOCKED
        )
        # Nothing was recorded: the suite never started.
        assert state.load_state(run_dir).gates == []
    finally:
        other.release()
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_run_with_its_own_database_does_not_queue_behind_the_shared_one(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    monkeypatch.setenv(locking.LOCK_DIR_ENV, str(tmp_path / "locks"))
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(lock_name="private-db"), run_dir)
    other = locking.FileLock(
        locking.shared_lock_path(locking.DEFAULT_SHARED_LOCK_NAME), purpose="gate"
    )
    other.acquire()
    try:
        assert (
            _run(
                repo,
                monkeypatch,
                ["gate", "--run-dir", str(run_dir), "--shared-lock-wait", "0"],
            )
            == cli.EXIT_OK
        )
    finally:
        other.release()


# ------------------------------------------------------------- timeouts


# --------------------------------------------------------- output encoding


def test_non_ascii_command_output_is_archived_as_utf8(
    repo: Path, monkeypatch, tmp_path
):
    """Decoding child output with the locale encoding mangles reviewer prose
    on Windows (cp1252) and can fail the capture outright.

    The ambient variables are cleared first: this suite runs under UTF-8, and
    a child inheriting that would pass whatever the runner does or does not
    set.
    """
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    printer = tmp_path / "printer.py"
    printer.write_text(
        "import sys\n"
        "sys.stdout.write('\\u4e2d\\u6587 caf\\u00e9 \\u2014 ok\\n')\n",
        encoding="utf-8",
    )
    brief = repo / "brief.toml"
    brief.write_text(
        _brief_text(commands=json.dumps([[sys.executable, str(printer)]])),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    log = Path(state.load_state(run_dir).gates[-1]["commands"][0]["log"])
    assert "\u4e2d\u6587 caf\u00e9 \u2014 ok" in log.read_bytes().decode("utf-8")


# ------------------------------------------------------------ pinned base


def test_an_explicit_base_is_stored_as_a_full_commit_not_a_name(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A base stored as "HEAD" or a branch re-points the moment anything is
    committed, so the review silently shrinks while the record still looks
    right."""
    merge_point = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "integration")
    (repo / "file.txt").write_text("package work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "package commit")

    run_dir = tmp_path / "run"
    assert (
        _run(
            repo,
            monkeypatch,
            [
                "start",
                "--brief", str(brief_file()),
                "--run-dir", str(run_dir),
                "--base", merge_point[:8],
                "--integration-ref", "integration",
            ],
        )
        == cli.EXIT_OK
    )
    saved = state.load_state(run_dir)
    assert saved.base == merge_point
    assert len(saved.base) == 40


def test_a_moving_reference_cannot_be_stored_as_the_base(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    assert (
        _run(
            repo,
            monkeypatch,
            [
                "start",
                "--brief", str(brief_file()),
                "--run-dir", str(run_dir),
                "--base", "HEAD",
                "--integration-ref", "main",
            ],
        )
        == cli.EXIT_OK
    )
    stored = state.load_state(run_dir).base
    assert stored == _git(repo, "rev-parse", "HEAD")
    (repo / "file.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later")
    # The base still names the commit it was pinned to, not the new HEAD.
    assert state.load_state(run_dir).base == stored != _git(repo, "rev-parse", "HEAD")


# -------------------------------------------------- run artefacts and digest


def test_run_artefacts_inside_the_repository_are_not_part_of_the_code_digest(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A run directory inside the repository and not ignored becomes part of
    the fingerprint of the code it is measuring: every log the gate writes
    changes the digest, so the approval that gate supports is stale before it
    is recorded and the run can never deliver."""
    run_dir = repo / "runs" / "r1"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))

    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert list(run_dir.glob("gate-01/*.log")), "the gate wrote logs inside the repository"
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    _triage_all(repo, monkeypatch, run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_real_source_change_still_invalidates_the_approval(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Excluding the run directory must not excuse the rest of the tree."""
    run_dir = repo / "runs" / "r1"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    (repo / "file.txt").write_text("changed after approval\n", encoding="utf-8")
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


# ------------------------------------------- the release condition itself


def _approve_with(findings_list: list[dict]) -> str:
    return _payload("needs-attention" if findings_list else "approve", findings_list=findings_list)


def test_structured_findings_are_read_from_the_plugins_own_result(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(tmp_path, outputs=[_approve_with([_structured_finding()])]),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    assert len(saved.findings) == 1
    recorded = findings.Finding(**saved.findings[0])
    assert recorded.severity == "high" and recorded.file == "file.txt"
    assert recorded.source == findings.SOURCE_STRUCTURED
    assert recorded.disposition == findings.PENDING and recorded.blocking
    assert saved.rounds[0].verdict == "needs-attention"


def test_an_approve_cannot_deliver_over_the_other_channels_blocking_finding(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The two channels disagree in practice. If either one's approval could
    release the other's findings, the workflow would systematically lose the
    findings only one channel ever sees."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert state.load_state(run_dir).rounds[0].verdict == "approve"

    assert (
        _run(
            repo,
            monkeypatch,
            [
                "findings", "record",
                "--run-dir", str(run_dir),
                "--round", "1",
                "--channel", runner.CHANNEL_FINDINGS,
                "--severity", "high",
                "--title", "unbounded retry loop",
                "--file", "file.txt",
                "--line", "3",
            ],
        )
        == cli.EXIT_OK
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    identifier = state.load_state(run_dir).findings[0]["id"]
    assert (
        _run(
            repo,
            monkeypatch,
            [
                "findings", "resolve",
                "--run-dir", str(run_dir),
                "--id", identifier,
                "--disposition", "refuted",
                "--note", "the loop is bounded by the caller's deadline; see line 40",
            ],
        )
        == cli.EXIT_OK
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_awaiting_adjudication_is_not_a_resolution(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _run(
        repo,
        monkeypatch,
        [
            "findings", "record",
            "--run-dir", str(run_dir),
            "--round", "1",
            "--channel", runner.CHANNEL_FINDINGS,
            "--severity", "critical",
            "--title", "writes truth without confirmation",
        ],
    )
    identifier = state.load_state(run_dir).findings[0]["id"]
    _run(
        repo,
        monkeypatch,
        [
            "findings", "resolve",
            "--run-dir", str(run_dir),
            "--id", identifier,
            "--disposition", "awaiting-adjudication",
            "--note", "needs the founder",
        ],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_channel_nobody_triaged_blocks_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Silence is not evidence that nobody found anything."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    # The structured channel triaged itself; the prose channel did not.
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    _run(
        repo,
        monkeypatch,
        [
            "findings", "none",
            "--run-dir", str(run_dir),
            "--round", "1",
            "--channel", runner.CHANNEL_FINDINGS,
            "--note", "read the archived prose; it reports nothing actionable",
        ],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_channel_cannot_be_both_clean_and_have_findings(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    args = [
        "findings", "none",
        "--run-dir", str(run_dir),
        "--round", "1",
        "--channel", runner.CHANNEL_FINDINGS,
        "--note", "nothing",
    ]
    assert _run(repo, monkeypatch, args) == cli.EXIT_OK
    assert (
        _run(
            repo,
            monkeypatch,
            [
                "findings", "record",
                "--run-dir", str(run_dir),
                "--round", "1",
                "--channel", runner.CHANNEL_FINDINGS,
                "--severity", "low",
                "--title", "a late thought",
            ],
        )
        == cli.EXIT_MISUSE
    )


def test_out_of_scope_work_is_escalated_and_never_recorded_as_fixed(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The reviewer is now told to report necessary work outside the approved
    scope rather than conceal it. This run may not do that work."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    marked = _structured_finding(
        severity="medium", title=f"{prompt.OUT_OF_SCOPE_MARKER}: the caller also leaks the handle"
    )
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[_approve_with([marked])])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    recorded = findings.Finding(**state.load_state(run_dir).findings[0])
    assert recorded.out_of_scope and recorded.blocking
    assert recorded.disposition == findings.AWAITING
    assert (
        _run(
            repo,
            monkeypatch,
            [
                "findings", "resolve",
                "--run-dir", str(run_dir),
                "--id", recorded.id,
                "--disposition", "fixed",
                "--note", "changed it anyway",
            ],
        )
        == cli.EXIT_BLOCKED
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_structured_findings_that_cannot_be_read_make_the_round_unusable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A finding the schema promised but that cannot be read must not be
    silently dropped — that is how a blocking defect would disappear between
    the reviewer and the release condition."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    broken = _structured_finding()
    broken.pop("severity")
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(tmp_path, outputs=[_payload("approve", findings_list=[broken])]),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert not state.load_state(run_dir).rounds[0].usable


def test_a_finding_that_is_not_an_object_makes_the_round_unusable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The other shape of the same failure: an entry that is not a finding at
    all. Skipping it would quietly shorten the list the release condition
    checks."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads(_payload("approve"))
    payload["result"]["findings"] = ["a bare string, not a finding"]
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert "does not satisfy" in saved.rounds[0].reason
    assert "findings[0]" in saved.rounds[0].reason  # the failing path is named
    assert saved.findings == []


# --------------------------------------------- structured verdict preferred


def test_the_structured_verdict_wins_over_prose_in_the_same_payload(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The structured result is what the plugin constrains with its own
    schema; a verdict line in the surrounding prose is not a second opinion."""
    payload = json.loads(_payload("needs-attention"))
    payload["codex"]["stdout"] = "everything is wonderful\n\nVerdict: approve\n"
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert state.load_state(run_dir).rounds[0].verdict == "needs-attention"


def test_a_payload_with_no_structured_result_and_no_verdict_line_is_unusable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    payload = {
        "review": "Adversarial Review",
        "codex": {"status": 0, "stdout": "I had a look and it seems fine to me overall", "stderr": ""},
        "result": None,
        "rawOutput": "",
        "parseError": "Codex did not return a final structured message.",
    }
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    # The envelope arrived; what it carried does not match the protocol, and
    # the prose beside it is not consulted.
    assert "does not match the plugin's protocol" in saved.rounds[0].reason


# ------------------------------------------------- the reviewer instructions


def test_the_reviewer_is_given_the_projects_judging_standard():
    text = prompt.review_instructions(
        package="p",
        base="abc",
        requirements=["r"],
        acceptance_criteria=["a"],
        allowed_paths=["p1"],
        prohibitions=["no merging"],
        gate_logs=["gate-01/x.log"],
        run_dir="run",
    )
    lowered = text.lower()
    assert "correctness" in lowered
    assert "maintainability" in lowered
    assert "project fit" in lowered
    # The two instructions that were removed, and must stay removed.
    assert "minimal in-scope fix" not in lowered
    assert "do not propose work outside" not in lowered
    assert "smaller change" in lowered  # it now says the opposite, explicitly


def test_the_reviewer_is_told_to_report_out_of_scope_work_not_hide_it():
    text = prompt.review_instructions(
        package="p",
        base="abc",
        requirements=["r"],
        acceptance_criteria=["a"],
        allowed_paths=["p1"],
        prohibitions=["no merging"],
        gate_logs=[],
        run_dir="run",
    )
    assert prompt.OUT_OF_SCOPE_MARKER in text
    assert "ask the founder for authorisation" in text
    assert "do not suppress it" in text.lower()


def test_a_failed_plugin_call_records_why_it_failed(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Found by running it: the real plugin exits 1 with the cause inside its
    payload ("You've hit your usage limit..."), and the run recorded only
    "review exited 1". An exhausted quota, a broken login and a crashed
    reviewer need different responses, so the run has to say which."""
    payload = {
        "review": "Adversarial Review",
        "codex": {"status": 1, "stdout": "", "stderr": ""},
        "result": None,
        "rawOutput": "",
        "parseError": "You've hit your usage limit. Try again at 5:29 PM.",
    }
    plugin = tmp_path / "failing_plugin.py"
    plugin.write_text(
        "import json, sys\n"
        f"sys.stdout.write(json.dumps({payload!r}))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, plugin)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert "usage limit" in saved.rounds[0].reason
    # And it is still a failure, not something the detail can rescue.
    assert saved.rounds[0].verdict == "unusable"


# =======================================================================
# Second correction pass: defects found by an independent review of the
# first one. Same rule as everything above - plant the failure, assert the
# workflow's action.
# =======================================================================


def test_an_empty_native_review_is_not_a_completed_channel(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The plugin exits 0 with an EMPTY review when a turn completes without
    producing review text - it renders that case itself as "Codex review
    completed without any stdout output". Under --json the envelope is still
    ~300 characters, so measuring the raw stdout counted the JSON wrapper as
    the review and let a channel that said nothing pass for a complete one."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("approve")],
            findings_output=_native_payload(""),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert "findings channel" in saved.rounds[0].reason
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_real_native_review_under_json_is_still_read(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The counterpart: a native channel that DID review must still count."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("approve")],
            findings_output=_native_payload(
                "I read the whole diff and have nothing material to report here."
            ),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert state.load_state(run_dir).rounds[0].usable


def test_a_dirty_tree_cannot_be_reviewed(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An explicit --base puts the plugin in branch mode, where the review
    input is the commit range base..HEAD. Uncommitted work is invisible to
    it, while the tree digest would bind the resulting approval to exactly
    that uncommitted work - an approval describing code nobody read."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])

    (repo / "file.txt").write_text("uncommitted work\n", encoding="utf-8")
    # Re-gated deliberately. With fresh verification covering this very tree
    # the staleness check is satisfied, so the dirty-tree rule is the only
    # thing left that can refuse; without this the test would pass for the
    # wrong reason and could not detect the rule being removed.
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert state.load_state(run_dir).review_count == 0  # the reviewer was never called

    _commit(repo, "commit the work")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_an_untracked_file_also_blocks_the_review(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    (repo / "new_module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_the_runs_own_directory_does_not_count_as_a_dirty_tree(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The run writes logs into its own directory while it works; those are
    evidence about the tree, not uncommitted work in it."""
    run_dir = repo / "runs" / "r1"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_completed_channel_in_an_unusable_round_must_still_be_triaged(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Usability is a whole-ROUND verdict that fails closed on any channel.
    Skipping every channel of an unusable round therefore skipped one that
    exited 0 and archived a complete review - which a later approving round
    could then deliver over, unread."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    # Round 1: the verdict channel dies, the prose channel reviews fine.
    plugin = tmp_path / "half_dead.py"
    plugin.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "counter = Path(__file__).with_suffix('.n')\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "channel = sys.argv[1]\n"
        "if channel == 'adversarial-review':\n"
        "    counter.write_text(str(n + 1))\n"
        "    if n == 0:\n"
        "        sys.stderr.write('reviewer unavailable\\n')\n"
        "        sys.exit(1)\n"
        f"    sys.stdout.write(json.loads(r'''{json.dumps(_payload('approve'))}'''))\n"
        "    sys.exit(0)\n"
        f"sys.stdout.write(json.loads(r'''{json.dumps(_native_payload('A long prose review that names a real problem in the code.'))}'''))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    _install_fake_plugin(monkeypatch, plugin)
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert saved.rounds[0].exit_codes[runner.CHANNEL_FINDINGS] == 0

    # Round 2 approves. The prose review from round 1 is still unread.
    # The tree is already clean; round 2 reviews the same head.
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "2",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read it"],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read the round-1 prose too"],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_unreadable_findings_do_not_attest_the_channel_as_read(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Writing "read, 0 findings" after a parse failure both misreports what
    was read and locks the agent out of recording the findings by hand: a
    channel cannot be attested clean and carry findings at the same time."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    broken = _structured_finding()
    broken["severity"] = "moderate"  # not one of the schema's four words
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(tmp_path, outputs=[_payload("approve", findings_list=[broken])]),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert saved.triage == []
    assert saved.findings == []
    # And the agent can now record what it read, by hand.
    assert (
        _run(
            repo,
            monkeypatch,
            ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
             "--channel", runner.CHANNEL_VERDICT, "--severity", "high",
             "--title", "unchecked index", "--file", "file.txt", "--line", "1"],
        )
        == cli.EXIT_OK
    )


def test_two_findings_with_the_same_title_and_line_stay_separately_resolvable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A shared id is not cosmetic: only the first is ever addressable, so
    the second can never be resolved and the run can never deliver."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    twin = _structured_finding(severity="high", title="same rule broken", line=7)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path, outputs=[_payload("needs-attention", findings_list=[twin, dict(twin)])]
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    ids = [f["id"] for f in saved.findings]
    assert len(ids) == 2 and len(set(ids)) == 2
    for identifier in ids:
        assert (
            _run(
                repo,
                monkeypatch,
                ["findings", "resolve", "--run-dir", str(run_dir), "--id", identifier,
                 "--disposition", "refuted", "--note", "checked the caller; cannot occur"],
            )
            == cli.EXIT_OK
        )
    assert all(f["disposition"] == "refuted" for f in state.load_state(run_dir).findings)


def test_a_closed_run_keeps_the_outcome_it_closed_with(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A delivered run's own record must not end up saying it was stopped."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert state.load_state(run_dir).status == "delivered"

    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert saved.status == "delivered"
    assert saved.stop_reason == ""


def test_a_timed_out_command_kills_a_worker_its_parent_left_behind(
    repo: Path, monkeypatch, tmp_path
):
    """The case that actually happens: a launcher (pytest, npm) exits and its
    worker keeps running. taskkill walks the LIVE parent chain, so once the
    direct child is gone its orphans cannot be reached from that PID - the
    kill silently does nothing. A job object (Windows) or the process group
    (POSIX) still reaches them.

    The worker deliberately inherits the pipes, which is what makes the
    command time out at all after its parent exited, and it outlives the
    post-kill drain window - an earlier version of this test used a worker
    that finished inside that window, so it passed either way.
    """
    beat = tmp_path / "beat.txt"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import time\n"
        "from pathlib import Path\n"
        f"target = Path(r'''{beat}''')\n"
        "for i in range(3000):\n"  # 300s: far longer than the drain window
        "    target.write_text(str(i))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, r'''{worker}'''])\n"
        "sys.exit(0)\n",  # the launcher exits immediately; the worker does not
        encoding="utf-8",
    )
    brief = repo / "brief.toml"
    brief.write_text(
        _brief_text(commands=json.dumps([[sys.executable, str(launcher)]])),
        encoding="utf-8",
    )
    _commit(repo, "brief")
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)

    assert (
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir), "--timeout", "4"])
        == cli.EXIT_BLOCKED
    )
    record = state.load_state(run_dir).gates[-1]["commands"][0]
    assert record["timed_out"] is True
    assert beat.exists(), "the worker never started; the test proves nothing"
    settled = beat.read_text()
    time.sleep(1.5)
    assert beat.read_text() == settled, "the orphaned worker outlived the timeout"
    assert record["killed_tree"] is True


def test_a_command_never_run_because_the_budget_expired_still_leaves_a_log(
    repo: Path, monkeypatch, tmp_path
):
    """A step with no log reads as a step that was never attempted; this one
    was never attempted for a specific reason, and the reason is the point."""
    brief = repo / "brief.toml"
    brief.write_text(
        _brief_text(
            commands=json.dumps(
                [
                    [sys.executable, "-c", "print('first')"],
                    [sys.executable, "-c", "print('second')"],
                ]
            )
        ),
        encoding="utf-8",
    )
    _commit(repo, "brief")
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief, run_dir)
    results = runner.run_gate_commands(
        [[sys.executable, "-c", "print('first')"], [sys.executable, "-c", "print('second')"]],
        cwd=repo,
        log_dir=run_dir / "gate-01",
        timeout_seconds=30,
        budget_seconds=0.0,
    )
    assert results[0].timed_out and results[0].log_path is not None
    text = Path(results[0].log_path).read_text(encoding="utf-8")
    assert "NOT RUN" in text and "budget was exhausted" in text


def test_the_fingerprint_covers_the_whole_repository_not_the_current_directory(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """git ls-files lists relative to the CURRENT directory, so a CLI run
    from a subdirectory used to fingerprint only that subtree - edits
    anywhere else left the digest unchanged and an approval kept applying."""
    sub = repo / "sub"
    sub.mkdir()
    (sub / "keep.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "add a subdirectory")

    run_dir = tmp_path / "run"
    brief = brief_file()
    monkeypatch.chdir(sub)
    assert (
        cli.main(
            ["start", "--brief", str(brief), "--run-dir", str(run_dir),
             "--integration-ref", "main"]
        )
        == cli.EXIT_OK
    )
    saved = state.load_state(run_dir)
    assert Path(saved.repo_root).resolve() == repo.resolve()

    before = state.tree_digest(Path(saved.repo_root))
    (repo / "elsewhere.txt").write_text("outside the subdirectory\n", encoding="utf-8")
    assert state.tree_digest(Path(saved.repo_root)) != before


def test_a_run_directory_holding_tracked_source_is_refused(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The run directory is excluded from the fingerprint, so pointing it at
    tracked source would hide that source from every staleness check."""
    run_dir = repo / "src"
    run_dir.mkdir()
    (run_dir / "module.py").write_text("x = 1\n", encoding="utf-8")
    _commit(repo, "tracked source")
    assert _start(repo, monkeypatch, brief_file(), run_dir) == cli.EXIT_MISUSE


def test_breaking_the_shared_gate_lock_is_recorded(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Machine-wide exclusion protects every OTHER run's database, so
    breaking it must not be invisible."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    locking.FileLock(
        locking.shared_lock_path(locking.DEFAULT_SHARED_LOCK_NAME), purpose="crashed"
    ).acquire()
    assert (
        _run(
            repo,
            monkeypatch,
            ["gate", "--run-dir", str(run_dir), "--break-shared-lock", "--shared-lock-wait", "0"],
        )
        == cli.EXIT_OK
    )
    events = state.load_state(run_dir).events
    assert any(e["event"] == "shared gate lock broken" for e in events)


def test_breaking_the_run_lock_from_a_findings_command_is_recorded(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    locking.FileLock(locking.run_lock_path(run_dir), purpose="crashed").acquire()
    assert (
        _run(
            repo,
            monkeypatch,
            ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
             "--channel", runner.CHANNEL_FINDINGS, "--note", "read it", "--break-lock"],
        )
        == cli.EXIT_OK
    )
    assert any(e["event"] == "run lock broken" for e in state.load_state(run_dir).events)


def test_releasing_a_lock_someone_else_now_holds_leaves_it_alone(tmp_path: Path):
    """A broken-and-retaken lock must survive the original holder's release,
    or a third process gets the same lock."""
    path = tmp_path / "x.lock"
    first = locking.FileLock(path, purpose="first")
    first.acquire()
    second = locking.FileLock(path, purpose="second")
    second.acquire(break_stale=True)  # the operator decided the first was stale
    first.release()
    assert path.exists(), "the first holder deleted the second holder's lock"
    assert json.loads(path.read_text(encoding="utf-8"))["purpose"] == "second"


def test_an_unreadable_lock_file_is_not_deleted_by_a_stale_holder(tmp_path: Path):
    path = tmp_path / "x.lock"
    held = locking.FileLock(path, purpose="first")
    held.acquire()
    path.write_text("{ truncated", encoding="utf-8")  # mid-write by someone else
    held.release()
    assert path.exists()


def test_an_unrecognised_structured_verdict_is_unusable(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads(_payload("approve"))
    payload["result"]["verdict"] = "looks fine to me"
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    reason = state.load_state(run_dir).rounds[0].reason
    assert "result.verdict" in reason and "is not one of" in reason


def test_a_plugin_that_never_started_still_records_why(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A failure early enough to produce no payload still leaves a message on
    the stream; a bare exit code sends the reader to the archive to learn
    something the run already knew."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, tmp_path / "does_not_exist.py")
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    reason = state.load_state(run_dir).rounds[0].reason
    assert "does_not_exist" in reason or "No such file" in reason or "cannot find" in reason


def test_findings_list_shows_dispositions_and_flags_the_blocking_ones(
    repo: Path, monkeypatch, brief_file, tmp_path, capsys
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path, outputs=[_payload("needs-attention", findings_list=[_structured_finding()])]
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    capsys.readouterr()
    assert _run(repo, monkeypatch, ["findings", "list", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "unchecked index" in printed and "pending" in printed
    assert "1 unresolved blocking finding" in printed

    assert (
        _run(repo, monkeypatch, ["findings", "list", "--run-dir", str(run_dir), "--json"])
        == cli.EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["severity"] == "high" and payload[0]["disposition"] == "pending"


def test_out_of_scope_work_reported_on_the_prose_channel_stops_the_run(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The escalation path for the channel nothing reads mechanically."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    assert (
        _run(
            repo,
            monkeypatch,
            ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
             "--channel", runner.CHANNEL_FINDINGS, "--severity", "low",
             "--title", "the caller leaks a handle", "--file", "other.py",
             "--out-of-scope"],
        )
        == cli.EXIT_OK
    )
    recorded = findings.Finding(**state.load_state(run_dir).findings[0])
    # Low severity, but out of scope: still blocking, and not resolvable here.
    assert recorded.out_of_scope and recorded.blocking
    assert recorded.disposition == findings.AWAITING
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert (
        _run(
            repo,
            monkeypatch,
            ["stop", "--run-dir", str(run_dir), "--reason",
             "out-of-scope change reported; needs founder authorisation"],
        )
        == cli.EXIT_OK
    )
    assert state.load_state(run_dir).status == "stopped"


def test_an_inherited_encoding_variable_does_not_survive_into_a_child(monkeypatch):
    """What this process inherited says nothing about how the children it
    spawns should encode output it is about to read."""
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    env = runner.child_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


# ------------------------------------------------- the structured fixtures
# ADR-001 section 5: one reviewer response per handled condition, with the
# action it must produce, asserted here. These are the --json payloads the
# plugin really prints, which is how it is always called.


@pytest.mark.parametrize(
    "verdict_fixture, prose_fixture, expected_exit, expected_verdict",
    [
        ("structured-approve.json", "native-prose.json", cli.EXIT_OK, "approve"),
        ("structured-blocking.json", "native-prose.json", cli.EXIT_OK, "needs-attention"),
        ("structured-malformed.json", "native-prose.json", cli.EXIT_BLOCKED, "unusable"),
        ("structured-missing.json", "native-prose.json", cli.EXIT_BLOCKED, "unusable"),
        ("structured-approve.json", "native-empty.json", cli.EXIT_BLOCKED, "unusable"),
    ],
)
def test_every_structured_fixture_maps_to_the_documented_action(
    repo: Path,
    monkeypatch,
    brief_file,
    tmp_path,
    verdict_fixture,
    prose_fixture,
    expected_exit,
    expected_verdict,
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[fixture(verdict_fixture)],
            findings_output=fixture(prose_fixture),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == expected_exit
    assert state.load_state(run_dir).rounds[0].verdict == expected_verdict


def test_the_blocking_fixture_is_recorded_with_its_severity_and_stops_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[fixture("structured-blocking.json")],
            findings_output=fixture("native-prose.json"),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    recorded = findings.Finding(**state.load_state(run_dir).findings[0])
    assert recorded.severity == "high" and recorded.blocking
    assert recorded.file == "app/services/example.py" and recorded.line_start == 142
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_the_out_of_scope_fixture_is_escalated_rather_than_implemented(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[fixture("structured-out-of-scope.json")],
            findings_output=fixture("native-prose.json"),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    recorded = findings.Finding(**state.load_state(run_dir).findings[0])
    assert recorded.out_of_scope and recorded.disposition == findings.AWAITING
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert (
        _run(
            repo,
            monkeypatch,
            ["findings", "resolve", "--run-dir", str(run_dir), "--id", recorded.id,
             "--disposition", "fixed", "--note", "did it anyway"],
        )
        == cli.EXIT_BLOCKED
    )


def test_the_documented_fixtures_all_exist():
    """A fixture named in the README but missing from disk is a promise the
    suite never keeps."""
    documented = {
        line.split("`")[1]
        for line in (FIXTURES / "README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    }
    on_disk = {p.name for p in FIXTURES.iterdir() if p.name != "README.md"}
    assert documented == on_disk, f"documented-only: {documented - on_disk}, undocumented: {on_disk - documented}"


def test_two_starts_cannot_race_on_one_run_directory(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The "already exists" check was a read followed by a write. Two starts
    could both pass it and then overwrite each other's state."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    brief = brief_file()
    held = locking.FileLock(locking.run_lock_path(run_dir), purpose="another start")
    held.acquire()
    try:
        assert _start(repo, monkeypatch, brief, run_dir) == cli.EXIT_BLOCKED
        assert not (run_dir / "run.json").exists()
    finally:
        held.release()
    assert _start(repo, monkeypatch, brief, run_dir) == cli.EXIT_OK


# =======================================================================
# Third pass: what the REAL reviewer found in the second one, over both
# channels (run .claude/handoff/self-v3, review 02).
# =======================================================================


def test_deleting_the_archived_evidence_blocks_the_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The run directory is excluded from the tree fingerprint - it has to
    be, or writing a gate log would invalidate the approval that log
    supports - so deleting the evidence moved nothing any other check looks
    at, and a delivery could cite raw output that is not there."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    assert state.load_state(run_dir).passing_round(repo, (run_dir,)) is not None

    archived = Path(state.load_state(run_dir).rounds[0].raw_paths[runner.CHANNEL_FINDINGS])
    archived.unlink()
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_replacing_the_archived_evidence_blocks_the_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Rewriting it is worse than deleting it: the file is still there, so
    only its content can give it away."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)

    archived = Path(state.load_state(run_dir).rounds[0].raw_paths[runner.CHANNEL_VERDICT])
    archived.write_text("a much more flattering review\n", encoding="utf-8")
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_missing_gate_log_blocks_the_next_review(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The reviewer is pointed at the gate logs; handing it a path to a file
    that no longer exists is what this workflow was built to stop doing."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    log = Path(state.load_state(run_dir).gates[-1]["commands"][0]["log"])
    log.unlink()
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert state.load_state(run_dir).review_count == 0


def _worker_pair(tmp_path: Path, beat: Path, immediate: bool):
    """A launcher that spawns a worker, and the worker.

    ``immediate`` makes the launcher spawn before doing anything else, which
    is the ordering that escapes a job assigned after the child is already
    running.
    """
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import time\n"
        "from pathlib import Path\n"
        f"target = Path(r\'\'\'{beat}\'\'\')\n"
        "for i in range(3000):\n"
        "    target.write_text(str(i))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, r\'\'\'{worker}\'\'\'],\n"
        "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        + ("" if immediate else "time.sleep(0.2)\n")
        + "sys.exit(0)\n",
        encoding="utf-8",
    )
    return launcher


def _wait_until_started(beat: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if beat.exists():
            return
        time.sleep(0.05)
    raise AssertionError("the worker never started; the test proves nothing")


def test_a_worker_left_behind_by_a_successful_command_is_still_cleaned_up(
    repo: Path, tmp_path
):
    """A command can exit 0 with its worker redirected away from the pipes,
    so nothing times out and communicate() returns at once. That worker would
    keep using the shared database after the gate released its lock - the
    failure the lock exists to prevent, arriving by another route."""
    beat = tmp_path / "beat.txt"
    launcher = _worker_pair(tmp_path, beat, immediate=False)
    result = runner.run_command(
        [sys.executable, str(launcher)],
        cwd=repo,
        timeout_seconds=30,
        log_path=tmp_path / "cmd.log",
    )
    assert result.ok  # the command itself succeeded
    _wait_until_started(beat)
    settled = beat.read_text()
    time.sleep(1.0)
    assert beat.read_text() == settled, "the leftover worker outlived the command"


def test_a_worker_spawned_before_adoption_is_still_contained(repo: Path, tmp_path, monkeypatch):
    """Containment must happen before the child can execute. Adoption is
    delayed here deliberately, which is the scheduling the reviewer inferred:
    a child already running can spawn a worker that no later assignment
    enrols."""
    beat = tmp_path / "beat.txt"
    launcher = _worker_pair(tmp_path, beat, immediate=True)

    original = runner.ProcessTree.adopt

    def slow_adopt(self, proc):
        time.sleep(1.0)  # the window a running child would use
        return original(self, proc)

    monkeypatch.setattr(runner.ProcessTree, "adopt", slow_adopt)
    result = runner.run_command(
        [sys.executable, str(launcher)],
        cwd=repo,
        timeout_seconds=30,
        log_path=tmp_path / "cmd.log",
    )
    assert result.ok
    if not beat.exists():
        return  # the worker never ran at all; nothing escaped either way
    settled = beat.read_text()
    time.sleep(1.0)
    assert beat.read_text() == settled, "a worker spawned before adoption escaped"


def test_a_child_that_cannot_be_contained_is_not_allowed_to_run(repo: Path, tmp_path, monkeypatch):
    """Fail closed: releasing an uncontained child would silently give back
    the guarantee the containment exists to provide."""
    marker = tmp_path / "ran.txt"
    script = tmp_path / "script.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path(r\'\'\'{marker}\'\'\').write_text('it ran')\n",
        encoding="utf-8",
    )

    def refuse(self, proc):
        raise runner.ContainmentError("simulated containment failure")

    monkeypatch.setattr(runner.ProcessTree, "adopt", refuse)
    result = runner.run_command(
        [sys.executable, str(script)],
        cwd=repo,
        timeout_seconds=30,
        log_path=tmp_path / "cmd.log",
    )
    assert not result.ok
    assert "could not be contained" in result.stderr
    time.sleep(0.5)
    assert not marker.exists(), "the uncontained child was allowed to run"




# =======================================================================
# Close-out: the four items left open when run self-v3 stopped at its
# round limit, and the regressions that pin them.
# =======================================================================


def _strip_digests(run_dir: Path, *, rounds: bool = True, gates: bool = True) -> None:
    """Rewrite the state exactly as a version without digests would have.

    Nothing on disk is touched: the archived logs stay byte for byte as they
    were, which is the point - the question is whether a record that cannot
    be verified may support a pass.
    """
    raw = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if rounds:
        for record in raw["rounds"]:
            record["raw_digests"] = {}
    if gates:
        for gate in raw["gates"]:
            for command in gate["commands"]:
                command.pop("log_sha256", None)
    (run_dir / "run.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")


def _approved_run(repo: Path, monkeypatch, brief_file, tmp_path, run_dir: Path) -> Path:
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    return run_dir


# ---- 1. evidence with no recorded digest must not support a pass --------


def test_a_review_without_a_recorded_digest_cannot_support_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Unverifiable is not the same as verified. A record whose digest was
    never taken cannot show that its archive is the archive the reviewer
    produced, so it may not be what a delivery rests on."""
    run_dir = _approved_run(repo, monkeypatch, brief_file, tmp_path, tmp_path / "run")
    _strip_digests(run_dir, gates=False)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_gate_without_a_recorded_digest_cannot_support_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = _approved_run(repo, monkeypatch, brief_file, tmp_path, tmp_path / "run")
    _strip_digests(run_dir, rounds=False)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_history_without_digests_stays_readable_and_is_never_rewritten(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Old runs keep loading and keep their records; what they cannot do is
    deliver. And nothing back-fills a digest for a log whose integrity was
    never recorded - computing one now would only prove the file has not
    changed since this moment, which is not what it would be claiming."""
    run_dir = _approved_run(repo, monkeypatch, brief_file, tmp_path, tmp_path / "run")
    before = {p.name: p.read_bytes() for p in run_dir.rglob("*.log")}
    _strip_digests(run_dir)

    reloaded = state.load_state(run_dir)
    assert reloaded.review_count == 1  # history intact
    assert reloaded.rounds[0].raw_digests == {}
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    # The archives are untouched, and stay untouched.
    assert {p.name: p.read_bytes() for p in run_dir.rglob("*.log")} == before
    assert state.load_state(run_dir).rounds[0].raw_digests == {}


def test_fresh_verifiable_evidence_restores_the_legitimate_path(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The way out is a fresh gate and review under the current version, not
    a back-filled hash."""
    run_dir = _approved_run(repo, monkeypatch, brief_file, tmp_path, tmp_path / "run")
    _strip_digests(run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    assert _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    _triage_all(repo, monkeypatch, run_dir)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


# ---- 2. persist each channel before the next one starts -----------------


def _interrupt_after_first_channel(monkeypatch):
    """Let the first channel finish, then die the way a killed session does."""
    real = runner.run_command
    seen: list[str] = []

    def wrapper(argv, **kwargs):
        channel = argv[2] if len(argv) > 2 else ""
        if seen:
            raise KeyboardInterrupt
        seen.append(channel)
        return real(argv, **kwargs)

    monkeypatch.setattr(runner, "run_command", wrapper)
    return seen


def test_a_completed_channel_is_persisted_before_the_next_one_starts(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An interruption between channels used to lose the first channel's exit
    code and digest entirely, so what it reported became unknowable."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _interrupt_after_first_channel(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])

    saved = state.load_state(run_dir)
    first = saved.rounds[-1]
    assert first.exit_codes.get(runner.CHANNEL_VERDICT) == 0
    assert first.raw_digests.get(runner.CHANNEL_VERDICT)
    assert Path(first.raw_paths[runner.CHANNEL_VERDICT]).exists()
    # And the channel that never ran is recorded as unknown, not as clean.
    assert first.channel_status[runner.CHANNEL_VERDICT] == "completed"
    assert first.channel_status[runner.CHANNEL_FINDINGS] == "pending"


def test_an_unknown_channel_result_is_not_treated_as_no_findings(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A later approving round must not deliver over a channel whose result
    nobody ever saw."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    undo = _interrupt_after_first_channel(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    monkeypatch.undo()
    assert undo  # the first channel really did run

    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "2",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read round 2's prose"],
    )
    # Round 1: its verdict channel COMPLETED and was archived but never
    # parsed, and its findings channel is unknown. Both have to be accounted
    # for before anything delivers.
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_VERDICT,
         "--note", "read its archive by hand; the structured result carried no findings"],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS,
         "--note", "the channel never ran; its archive is absent and nothing is inferred from it"],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    # The record distinguishes the two attestations rather than blurring them.
    attestations = {
        (int(a["round"]), a["channel"]): a for a in state.load_state(run_dir).triage
    }
    assert attestations[(1, runner.CHANNEL_VERDICT)]["incomplete"] is False
    assert attestations[(1, runner.CHANNEL_FINDINGS)]["incomplete"] is True
    assert attestations[(1, runner.CHANNEL_FINDINGS)]["channel_status"] == "pending"


# ---- 3. evidence paths are absolute -------------------------------------


def test_a_run_started_with_a_relative_directory_resumes_from_anywhere(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A relative --run-dir used to record relative evidence paths, so
    resuming from another directory resolved them against the wrong root and
    reported existing logs as missing."""
    monkeypatch.chdir(repo)
    brief = brief_file()
    assert cli.main(
        ["start", "--brief", str(brief), "--run-dir", "runs/here",
         "--integration-ref", "main"]
    ) == cli.EXIT_OK
    run_dir = (repo / "runs" / "here").resolve()
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    assert cli.main(["gate", "--run-dir", "runs/here"]) == cli.EXIT_OK
    assert cli.main(["review", "--run-dir", "runs/here"]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    for path in saved.rounds[0].raw_paths.values():
        assert Path(path).is_absolute(), path
    for command in saved.gates[-1]["commands"]:
        assert Path(command["log"]).is_absolute(), command["log"]

    # Resume from somewhere else entirely, addressing the run absolutely.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(
        ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read from elsewhere"]
    ) == cli.EXIT_OK
    assert cli.main(["status", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    assert cli.main(["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


# ---- 4. findings shapes -------------------------------------------------


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda r: r.pop("findings"), "the field is absent"),
        (lambda r: r.update(findings=None), "the field is null"),
        (lambda r: r.update(findings={}), "the field is an object"),
        (lambda r: r.update(findings="none"), "the field is a string"),
        (lambda r: r.pop("verdict"), "the verdict is absent"),
        (lambda r: r.update(summary=None), "the summary is null"),
        (lambda r: r.pop("next_steps"), "next_steps is absent"),
    ],
)
def test_an_incomplete_structured_result_never_passes(
    repo: Path, monkeypatch, brief_file, tmp_path, mutate, why
):
    """Only an explicitly empty list, in a result that matches the plugin's
    actual protocol, means "no findings". Absent, null and the wrong type are
    each a different thing and none of them is evidence of a clean review."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads(_payload("approve"))
    mutate(payload["result"])
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED, why

    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable, why
    assert saved.triage == [], why  # never attested as read
    assert saved.findings == [], why


def test_an_explicitly_empty_findings_list_is_a_clean_review(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The legitimate path, stated as its own case so the rule above cannot
    be tightened into refusing everything."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert saved.rounds[0].verdict == "approve" and saved.rounds[0].usable
    assert saved.findings == []
    assert [t["channel"] for t in saved.triage] == [runner.CHANNEL_VERDICT]


def test_a_prose_verdict_cannot_rescue_an_incomplete_structured_result(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The payload says the reviewer was asked for a structured result. If
    what came back does not match the protocol, a verdict line in the
    surrounding text is not a second opinion - it is the failure the schema
    exists to catch, wearing a different hat."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    payload = json.loads(_payload("approve"))
    payload["result"] = None
    payload["parseError"] = "Codex did not return a final structured message."
    payload["codex"]["stdout"] = (
        "I read the whole change and it looks good to me.\n\nVerdict: approve\n"
    )
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert not state.load_state(run_dir).rounds[0].usable


def test_a_legacy_relative_artefact_path_is_rebased_onto_the_run_directory(tmp_path: Path):
    """Runs written before paths were absolute recorded them relative to the
    invocation directory. Everything a run archives lives under its own
    directory, so such a path is rebased onto the directory actually given -
    otherwise a real run recorded as ".claude/handoff/self-v3/review-01.log"
    becomes unreadable the moment anything resumes it from elsewhere."""
    run_dir = tmp_path / "handoff" / "self-v9"
    (run_dir / "gate-01").mkdir(parents=True)
    log = run_dir / "gate-01" / "gate-01-ruff.log"
    log.write_bytes(b"ok\n")

    legacy = ".claude/handoff/self-v9/gate-01/gate-01-ruff.log"
    assert state.artefact_path(run_dir, legacy) == log
    assert state.file_digest(state.artefact_path(run_dir, legacy))

    # An absolute record is left exactly as it is.
    assert state.artefact_path(tmp_path / "somewhere-else", str(log)) == log


# =======================================================================
# Direct regressions of the close-out items, reported by the real reviewer
# on the close-out itself (run self-v4, review 01).
# =======================================================================


MALFORMED_WITH_APPROVAL = (
    '{"review": "Adversarial Review", "result": {"verdict": "approve", "summary": "ok"\n'
    "\nI read the change and it looks fine.\n\nVerdict: approve\n"
)


def test_malformed_plugin_json_cannot_be_talked_into_an_approval(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """CLOSE-OUT 4, the hole left in it: output that begins as JSON and does
    not parse was indistinguishable from a plugin that never emitted JSON at
    all, so it fell through to the prose reader - and a 'Verdict: approve'
    line after the broken object became an approval."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[MALFORMED_WITH_APPROVAL])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert "not a JSON envelope" in saved.rounds[0].reason
    assert saved.triage == [] and saved.findings == []
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def _finished_run_with_an_open_finding(
    repo: Path, monkeypatch, brief_file, tmp_path, run_dir: Path
) -> Path:
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path, outputs=[_payload("needs-attention", findings_list=[_structured_finding()])]
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _run(
        repo,
        monkeypatch,
        ["stop", "--run-dir", str(run_dir), "--reason", "stopped with the finding open"],
    )
    return run_dir


def test_a_linked_runs_open_items_survive_finishing_from_another_directory(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """CLOSE-OUT 3, the half about carrying the older run forward: the link
    was stored exactly as typed, so a relative one resolved against whatever
    directory the finish ran in, the load failed, and the delivery silently
    reported no carried items at all."""
    # Run directories live where a real one does: inside the repository and
    # ignored, so another run's artefacts are not uncommitted work in this one.
    (repo / ".gitignore").write_text("runs/\n", encoding="utf-8")
    _commit(repo, "ignore run directories")
    older = _finished_run_with_an_open_finding(
        repo, monkeypatch, brief_file, tmp_path, repo / "runs" / "older"
    )
    assert state.load_state(older).findings  # it really has an open item

    monkeypatch.chdir(repo)
    newer = repo / "runs" / "newer"
    assert cli.main(
        ["start", "--brief", "brief.toml", "--run-dir", "runs/newer",
         "--linked-run", "runs/older", "--integration-ref", "main"]
    ) == cli.EXIT_OK
    assert Path(state.load_state(newer).linked_run).is_absolute()

    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    assert cli.main(["gate", "--run-dir", "runs/newer"]) == cli.EXIT_OK
    assert cli.main(["review", "--run-dir", "runs/newer"]) == cli.EXIT_OK
    assert cli.main(
        ["findings", "none", "--run-dir", str(newer), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read it"]
    ) == cli.EXIT_OK

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # The inherited item is blocking, so it gates - and that gate has to work
    # from here too, which is the point of resolving the paths.
    assert cli.main(["finish", "--run-dir", str(newer)]) == cli.EXIT_BLOCKED
    inherited = cli._linked_unresolved(state.load_state(newer))
    assert inherited, "the older run's open item vanished between records"
    assert inherited[0]["severity"] == "high"
    assert cli.main(
        ["findings", "resolve", "--run-dir", str(newer), "--id", inherited[0]["id"],
         "--origin-run", inherited[0]["origin_run"], "--disposition", "fixed",
         "--note", "closed by commit abc1234, recorded from another directory"]
    ) == cli.EXIT_OK
    assert cli.main(["finish", "--run-dir", str(newer)]) == cli.EXIT_OK

    summary = json.loads((newer / "delivery.json").read_text(encoding="utf-8"))
    assert summary["linked_unresolved"] == []
    carried = summary["carried_history"]
    assert carried and carried[0]["severity"] == "high"
    assert carried[0]["resolved_later"]["disposition"] == "fixed"


def test_a_linked_run_that_cannot_be_read_blocks_instead_of_reporting_none(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An unreadable link and a link with nothing open produced the same
    empty list, so losing the carried items looked exactly like having none."""
    older = _finished_run_with_an_open_finding(
        repo, monkeypatch, brief_file, tmp_path, tmp_path / "older"
    )
    brief = repo / "brief.toml"  # already committed by the fixture above
    newer = tmp_path / "newer"
    assert _run(
        repo,
        monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(newer),
         "--linked-run", str(older), "--integration-ref", "main"],
    ) == cli.EXIT_OK
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(newer)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(newer)])
    _triage_all(repo, monkeypatch, newer)

    (older / "run.json").unlink()
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(newer)]) == cli.EXIT_BLOCKED


def test_a_linked_run_that_does_not_load_is_refused_at_start(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Caught where it can still be corrected cheaply, rather than at the end."""
    empty = tmp_path / "not-a-run"
    empty.mkdir()
    assert _run(
        repo,
        monkeypatch,
        ["start", "--brief", str(brief_file()), "--run-dir", str(tmp_path / "newer"),
         "--linked-run", str(empty), "--integration-ref", "main"],
    ) == cli.EXIT_MISUSE
    assert not (tmp_path / "newer" / "run.json").exists()


# =======================================================================
# The call contract. Every channel is invoked with --json, so every channel
# owes a JSON envelope; anything else is a refusal, not a dialect.
# =======================================================================


NON_CONFORMING_APPROVAL = fixture("non-conforming-approval.txt")


def test_non_conforming_output_with_an_approval_line_is_refused(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The controlled input: a banner, a truncated object, then
    "Verdict: approve". It does not begin with "{", so a first-character test
    classified it as a plugin that never emits JSON and the prose reader took
    the approval. The call asked for JSON; this is not JSON."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[NON_CONFORMING_APPROVAL])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED

    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert saved.rounds[0].verdict == "unusable"
    assert "not a JSON envelope" in saved.rounds[0].reason
    assert saved.triage == [] and saved.findings == []
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


@pytest.mark.parametrize(
    "stdout, why",
    [
        ("Everything is fine.\n\nVerdict: approve\n", "prose with an approval line"),
        ('{"result": {"verdict": "approve"', "a truncated object"),
        ('["approve"]', "JSON that is not an object"),
        ("", "nothing at all"),
        ("   \n\n  ", "whitespace"),
        ('{"result": {"verdict": "approve"} } trailing junk', "an object plus trailing text"),
    ],
)
def test_every_non_envelope_shape_yields_no_verdict(stdout, why):
    """One rule, applied at the boundary, rather than a test per shape in the
    reader. None of these is a dialect to be accommodated."""
    result = runner.CommandResult(
        argv=["node", "plugin.mjs", "adversarial-review", "--wait", "--json"],
        exit_code=0,
        stdout=stdout,
        stderr="",
        duration_seconds=1.0,
        timed_out=False,
    )
    assert result.envelope() is None, why
    read = verdict.read_channel(
        envelope=result.envelope(),
        raw_text=result.combined,
        exit_code=0,
        timed_out=False,
        expects_verdict=True,
    )
    assert read.ok is False, why
    assert read.verdict.value == verdict.UNUSABLE, why
    assert read.verdict.usable is False, why


def test_an_approval_on_stderr_is_never_read_as_a_verdict():
    """stderr is diagnostic. It explains a failure; it does not decide one."""
    result = runner.CommandResult(
        argv=["node", "plugin.mjs", "adversarial-review", "--wait", "--json"],
        exit_code=0,
        stdout="",
        stderr="Verdict: approve\n" * 5,
        duration_seconds=1.0,
        timed_out=False,
    )
    read = verdict.read_channel(
        envelope=result.envelope(),
        raw_text=result.combined,
        exit_code=0,
        timed_out=False,
        expects_verdict=True,
    )
    assert read.ok is False and read.verdict.value == verdict.UNUSABLE


def test_a_valid_envelope_is_the_way_through(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The legitimate path, stated as its own case so the rule above cannot
    harden into refusing everything."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[fixture("structured-approve.json")],
            findings_output=fixture("native-prose.json"),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK

    saved = state.load_state(run_dir)
    assert saved.rounds[0].verdict == "approve" and saved.rounds[0].usable
    _run(
        repo,
        monkeypatch,
        ["findings", "none", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--note", "read the archived prose"],
    )
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_a_verdict_outside_the_protocols_enum_is_refused(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """A valid envelope is not enough: the verdict itself comes from the
    plugin's enum, and an unfamiliar word is not guessed at."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch, _write_fake_plugin(tmp_path, outputs=[fixture("structured-bad-verdict.json")])
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    reason = state.load_state(run_dir).rounds[0].reason
    assert "result.verdict" in reason and "is not one of" in reason


def test_the_native_channel_reads_its_review_from_inside_the_envelope(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The native channel keeps the contract too, and its prose is still
    read - from codex.stdout, not from the raw stream."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    body = (
        "One thing worth a look: the retry loop has no ceiling when the "
        "caller passes no deadline."
    )
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path, outputs=[_payload("approve")], findings_output=_native_payload(body)
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK
    archived = Path(state.load_state(run_dir).rounds[0].raw_paths[runner.CHANNEL_FINDINGS])
    assert body in archived.read_text(encoding="utf-8")


def test_the_native_channel_also_refuses_non_conforming_output(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Both channels are called with --json, so both owe an envelope."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("approve")],
            findings_output="# Codex Review\n\nNothing to report on this head at all.\n",
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    assert not state.load_state(run_dir).rounds[0].usable


def test_no_first_character_test_survives_in_the_reader():
    """The rule this replaced was a startswith check. Keeping it out is the
    point, so it is asserted rather than trusted."""
    source = (REPO_ROOT / "scripts/review_handoff/verdict.py").read_text(encoding="utf-8")
    runner_source = (REPO_ROOT / "scripts/review_handoff/runner.py").read_text(encoding="utf-8")
    assert "startswith" not in source
    assert "startswith" not in runner_source.split("def envelope")[1].split("def ")[0]


# =======================================================================
# 1. Full protocol validation, by a standard JSON Schema validator against
#    the pinned plugin schema.
# =======================================================================


def test_the_vendored_protocol_is_pinned_and_recorded():
    """CI has no plugin, so it validates against the vendored copy. The pin
    is the plugin version and a canonical digest - canonical because git
    normalises line endings on checkout, so a raw-byte digest of a vendored
    file would break the moment CI cloned it."""
    record = findings.protocol()
    assert record["plugin"] == "codex@openai-codex"
    assert record["plugin_version"] == "1.0.6"
    assert record["json_schema_draft"] == "https://json-schema.org/draft/2020-12/schema"
    raw = (findings.PROTOCOL_DIR / record["schema"]).read_bytes()
    assert findings.canonical_digest(raw) == record["canonical_sha256"]


@pytest.mark.skipif(
    not runner.DEFAULT_PLUGIN_SCRIPT.exists(),
    reason="the installed plugin is needed to compare the vendored protocol against it",
)
def test_the_vendored_protocol_still_matches_the_installed_plugin():
    """A plugin upgrade must surface as a failure here, not as silent drift
    between what the reviewer answers against and what this validates."""
    record = findings.protocol()
    installed = runner.DEFAULT_PLUGIN_SCRIPT.parent.parent / "schemas" / record["schema"]
    assert installed.exists(), installed
    assert findings.canonical_digest(installed.read_bytes()) == record["canonical_sha256"], (
        "the installed plugin's schema differs from the vendored copy; re-vendor it "
        "and re-read what changed before trusting either"
    )


def test_the_workflow_knows_what_to_do_with_every_verdict_the_protocol_allows():
    """The schema confines the verdict; this is the separate question of
    whether this workflow has a rule for each permitted value. A protocol
    that gains one must not be acted on until someone decides what it means."""
    allowed = set(findings.protocol_schema()["properties"]["verdict"]["enum"])
    assert allowed == set(verdict._STRUCTURED_VERDICTS)


@pytest.mark.parametrize(
    "mutate, where",
    [
        (lambda r: r["next_steps"].append(None), "next_steps[0]"),
        (lambda r: r["next_steps"].append(""), "next_steps[0]"),
        (lambda r: r["findings"].append({"severity": "medium", "title": "x"}), "findings[0]"),
        (
            lambda r: r["findings"].append(dict(_structured_finding(), confidence=2.0)),
            "findings[0].confidence",
        ),
        (
            lambda r: r["findings"].append(dict(_structured_finding(), line_start=0)),
            "findings[0].line_start",
        ),
        (
            lambda r: r["findings"].append(dict(_structured_finding(), severity="moderate")),
            "findings[0].severity",
        ),
        (
            lambda r: r["findings"].append(dict(_structured_finding(), extra="surprise")),
            "findings[0]",
        ),
        (lambda r: r.update(summary=""), "result.summary"),
        (lambda r: r.update(surprise="extra"), "result"),
    ],
)
def test_the_whole_schema_is_validated_not_just_the_top_level(mutate, where):
    """Nested objects, array items, required fields, types, enums and bounds.
    One validator over the whole document, rather than a special case per
    example."""
    result = {
        "verdict": "approve",
        "summary": "clean",
        "findings": [],
        "next_steps": [],
    }
    mutate(result)
    with pytest.raises(findings.FindingsError) as info:
        findings.validate_structured_result(result)
    assert where in str(info.value), str(info.value)


def test_a_result_that_satisfies_the_schema_is_read_exactly_as_given():
    """The legitimate path: nothing is defaulted, coerced or dropped."""
    entry = _structured_finding(severity="critical", title="real defect", line=42)
    result = {
        "verdict": "needs-attention",
        "summary": "one blocking defect",
        "findings": [entry],
        "next_steps": ["fix it"],
    }
    got = findings.findings_from_structured(
        result, round_number=1, channel="adversarial-review", recorded_at="now"
    )
    assert len(got) == 1
    only = got[0]
    assert only.severity == "critical" and only.title == "real defect"
    assert only.file == entry["file"] and only.line_start == 42 and only.line_end == 42
    assert only.body == entry["body"] and only.recommendation == entry["recommendation"]
    assert only.confidence == entry["confidence"]


def test_a_malformed_result_yields_no_verdict_and_no_findings(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """End to end: the two samples the reviewer reproduced."""
    brief = brief_file()
    for index, result in enumerate(
        (
            {"verdict": "approve", "summary": "ok", "findings": [], "next_steps": [None]},
            {
                "verdict": "approve",
                "summary": "ok",
                "findings": [{"severity": "medium", "title": "something"}],
                "next_steps": [],
            },
        )
    ):
        run_dir = tmp_path / f"run-{index}"
        _run(
            repo, monkeypatch,
            ["start", "--brief", str(brief), "--run-dir", str(run_dir),
             "--integration-ref", "main"],
        )
        payload = json.loads(_payload("approve"))
        payload["result"] = result
        _install_fake_plugin(
            monkeypatch, _write_fake_plugin(tmp_path, outputs=[json.dumps(payload)])
        )
        _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
        assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
        saved = state.load_state(run_dir)
        assert not saved.rounds[0].usable
        assert saved.findings == [] and saved.triage == []
        assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


# =======================================================================
# 2. The whole linked chain, with history and later evidence kept apart.
# =======================================================================


def _run_with_open_finding(repo, monkeypatch, brief, tmp_path, run_dir, linked=None):
    args = ["start", "--brief", str(brief), "--run-dir", str(run_dir),
            "--integration-ref", "main"]
    if linked is not None:
        args += ["--linked-run", str(linked)]
    assert _run(repo, monkeypatch, args) == cli.EXIT_OK
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["stop", "--run-dir", str(run_dir), "--reason", "stopped open"])
    return run_dir


def test_the_chain_is_followed_through_three_generations(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """C continues B continues A. A's open item must reach C's delivery -
    reading only the immediate predecessor lost it."""
    brief = brief_file()
    a = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    b = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "b", linked=a)
    c = tmp_path / "c"
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(c),
         "--linked-run", str(b), "--integration-ref", "main"],
    )
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(c)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(c)])
    _triage_all(repo, monkeypatch, c)

    # Inherited blocking findings are blocking findings. Reporting them
    # without enforcing them would make the carry-forward decorative.
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(c)]) == cli.EXIT_BLOCKED

    inherited = cli._carried_history(state.load_state(c))
    assert {item["origin_run"] for item in inherited} == {"a", "b"}, inherited
    for item in inherited:
        assert item["id"].startswith("r01-")  # original finding ids preserved
        assert item["still_open"] is True
        assert _run(
            repo, monkeypatch,
            ["findings", "resolve", "--run-dir", str(c), "--id", item["id"],
             "--origin-run", item["origin_run"], "--disposition", "fixed",
             "--note", f"closed for {item['origin_run']} by commit abc1234"],
        ) == cli.EXIT_OK

    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(c)]) == cli.EXIT_OK
    summary = json.loads((c / "delivery.json").read_text(encoding="utf-8"))
    assert summary["linked_unresolved"] == []
    assert {i["origin_run"] for i in summary["carried_history"]} == {"a", "b"}


@pytest.mark.parametrize(
    "break_it, expected",
    [
        (lambda a, b: shutil.rmtree(a), "no longer there"),
        (lambda a, b: (a / "run.json").write_text("{ truncated", encoding="utf-8"), "cannot be read"),
    ],
)
def test_a_chain_that_cannot_be_read_never_looks_like_an_empty_one(
    repo: Path, monkeypatch, brief_file, tmp_path, break_it, expected
):
    """The distinction the whole mechanism turns on: 'nothing is open' and
    'nobody can tell what is open' are different answers."""
    brief = brief_file()
    a = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    b = tmp_path / "b"
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(b),
         "--linked-run", str(a), "--integration-ref", "main"],
    )
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(b)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(b)])
    _triage_all(repo, monkeypatch, b)

    break_it(a, b)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_BLOCKED
    walk = state.linked_chain(state.load_state(b))
    assert walk.problems and expected in walk.problems[0]


def test_a_chain_that_loops_is_reported_rather_than_followed(tmp_path: Path):
    """A cycle would otherwise walk for ever or silently truncate."""
    one, two = tmp_path / "one", tmp_path / "two"
    for path, other, run_id in ((one, two, "one"), (two, one, "two")):
        st = state.new_state(
            run_id=run_id, brief_name="b", brief_path=tmp_path / "brief.toml",
            brief_digest="d", repo_root=tmp_path, base="0" * 40,
            max_review_rounds=1, max_total_seconds=60, linked_run=str(other),
        )
        st.save(path)
    walk = state.linked_chain(state.load_state(one))
    assert walk.problems and "loops back" in walk.problems[0]


def test_an_item_closed_later_is_not_reported_as_a_current_defect(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The older record is never rewritten, so its own status stays what it
    was. The later evidence is recorded separately, and the two are reported
    as two things rather than merged into one misleading one."""
    brief = brief_file()
    a = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    ancestral = state.load_state(a).findings[0]["id"]
    before = (a / "run.json").read_bytes()

    b = tmp_path / "b"
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(b),
         "--linked-run", str(a), "--integration-ref", "main"],
    )
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(b)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(b)])
    _triage_all(repo, monkeypatch, b)

    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--disposition", "fixed", "--note", "closed by commit abc1234 with its test"],
    ) == cli.EXIT_OK
    assert (a / "run.json").read_bytes() == before, "the older record was rewritten"

    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_OK
    summary = json.loads((b / "delivery.json").read_text(encoding="utf-8"))
    assert summary["linked_unresolved"] == []  # not a current defect any more
    carried = summary["carried_history"]
    assert len(carried) == 1
    assert carried[0]["disposition"] == "pending"  # what a's own record still says
    assert carried[0]["resolved_later"]["disposition"] == "fixed"
    assert "abc1234" in carried[0]["resolved_later"]["note"]
    assert carried[0]["still_open"] is False


# =======================================================================
# 3. One defect, two channels, one item.
# =======================================================================


def test_one_defect_seen_by_both_channels_counts_once_and_keeps_both_records(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    primary = state.load_state(run_dir).findings[0]["id"]

    assert _run(
        repo, monkeypatch,
        ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--severity", "medium",
         "--title", "the same unchecked index", "--file", "file.txt", "--line", "1",
         "--duplicate-of", primary],
    ) == cli.EXIT_OK

    records = [findings.Finding(**f) for f in state.load_state(run_dir).findings]
    assert len(records) == 2  # both pieces of evidence kept
    blocking = findings.unresolved_blocking(records)
    assert [f.id for f in blocking] == [primary]  # counted once, under the primary
    assert [f.channel for f in findings.duplicates_of(records, primary)] == [
        runner.CHANNEL_FINDINGS
    ]

    # Resolving the primary closes the pair.
    _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(run_dir), "--id", primary,
         "--disposition", "fixed", "--note", "fixed with a regression"],
    )
    records = [findings.Finding(**f) for f in state.load_state(run_dir).findings]
    assert findings.unresolved_blocking(records) == []
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_duplicate_does_not_soften_a_more_severe_sighting(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """If the second channel rates it lower, the group still blocks."""
    low = findings.Finding(id="p", round=1, channel="review", severity="low", title="x")
    high = findings.Finding(
        id="d", round=1, channel="adversarial-review", severity="high", title="x",
        duplicate_of="p",
    )
    blocking = findings.unresolved_blocking([low, high])
    assert [f.id for f in blocking] == ["p"]


def test_a_duplicate_must_point_at_another_channels_record(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    primary = state.load_state(run_dir).findings[0]["id"]

    # Unknown target.
    assert _run(
        repo, monkeypatch,
        ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--severity", "low", "--title", "y",
         "--duplicate-of", "r99-nope-deadbeef"],
    ) == cli.EXIT_MISUSE

    # Same channel as the target.
    assert _run(
        repo, monkeypatch,
        ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_VERDICT, "--severity", "low", "--title", "z",
         "--duplicate-of", primary],
    ) == cli.EXIT_MISUSE


# =======================================================================
# Round 2: what the real reviewer found in the history tracking itself
# (run self-v6, review 01).
# =======================================================================


def _ancestor_with_open_finding(repo, monkeypatch, brief, tmp_path, run_dir):
    return _run_with_open_finding(repo, monkeypatch, brief, tmp_path, run_dir)


def _successor(repo, monkeypatch, brief, tmp_path, run_dir, linked):
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(run_dir),
         "--linked-run", str(linked), "--integration-ref", "main"],
    )
    _install_fake_plugin(monkeypatch, _write_fake_plugin(tmp_path, outputs=[_payload("approve")]))
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    _triage_all(repo, monkeypatch, run_dir)
    return run_dir


@pytest.mark.parametrize("disposition", ["pending", "awaiting-adjudication"])
def test_a_nonterminal_carried_disposition_does_not_close_the_item(
    repo: Path, monkeypatch, brief_file, tmp_path, disposition
):
    """Recording that an inherited defect is still pending, or is waiting on
    the founder, is not a closure. Treating any carried entry as one made
    saying "this still needs a decision" the way to make it disappear."""
    brief = brief_file()
    a = _ancestor_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    ancestral = state.load_state(a).findings[0]["id"]
    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)

    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--disposition", disposition, "--note", "still needs a decision"],
    ) == cli.EXIT_OK

    summary_state = state.load_state(b)
    carried = cli._carried_history(summary_state)
    assert len(carried) == 1
    assert carried[0]["still_open"] is True, carried[0]
    assert cli._linked_unresolved(summary_state), "the item vanished from the open list"


def test_a_carried_item_can_be_closed_after_a_nonterminal_entry(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """And the non-terminal entry must not lock the item: a later, real
    closure has to be recordable."""
    brief = brief_file()
    a = _ancestor_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    ancestral = state.load_state(a).findings[0]["id"]
    before = (a / "run.json").read_bytes()
    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)

    _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--disposition", "awaiting-adjudication", "--note", "asked the founder"],
    )
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--disposition", "fixed", "--note", "authorised, then fixed in commit abc1234"],
    ) == cli.EXIT_OK

    saved = state.load_state(b)
    carried = cli._carried_history(saved)
    assert carried[0]["still_open"] is False
    assert carried[0]["resolved_later"]["disposition"] == "fixed"
    assert "abc1234" in carried[0]["resolved_later"]["note"]
    # Both entries are kept: the trail of what was decided, and when.
    assert len(saved.carried_resolutions) == 2
    assert (a / "run.json").read_bytes() == before


def test_an_ambiguous_finding_id_across_runs_must_be_qualified(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Ids carry the round and channel but not the run, so the same defect
    reported again in a later run collides. Resolving picked the nearest and
    the older one could never be addressed."""
    brief = brief_file()
    a = _ancestor_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    b = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "b", linked=a)
    shared = state.load_state(a).findings[0]["id"]
    assert state.load_state(b).findings[0]["id"] == shared, "the ids should collide here"

    c = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "c", b)

    # Unqualified: ambiguous, and refused rather than guessed at.
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(c), "--id", shared,
         "--disposition", "fixed", "--note", "which one?"],
    ) == cli.EXIT_MISUSE

    # Qualified: the older generation is reachable.
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(c), "--id", shared,
         "--origin-run", "a", "--disposition", "fixed", "--note", "closed for a"],
    ) == cli.EXIT_OK
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(c), "--id", shared,
         "--origin-run", "b", "--disposition", "refuted", "--note", "closed for b"],
    ) == cli.EXIT_OK

    carried = {item["origin_run"]: item for item in cli._carried_history(state.load_state(c))}
    assert carried["a"]["resolved_later"]["disposition"] == "fixed"
    assert carried["b"]["resolved_later"]["disposition"] == "refuted"
    assert all(item["still_open"] is False for item in carried.values())


def test_an_unknown_origin_run_is_refused(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    brief = brief_file()
    a = _ancestor_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    ancestral = state.load_state(a).findings[0]["id"]
    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--origin-run", "nowhere", "--disposition", "fixed", "--note", "x"],
    ) == cli.EXIT_MISUSE


def test_linking_a_duplicate_cannot_bypass_the_out_of_scope_stop(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An out-of-scope finding needs the founder. Recording it as a duplicate
    of an in-scope primary and then closing the primary would have retired
    the pair without anyone being asked."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    primary = state.load_state(run_dir).findings[0]["id"]

    assert _run(
        repo, monkeypatch,
        ["findings", "record", "--run-dir", str(run_dir), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--severity", "low",
         "--title", "the caller must change too", "--file", "other.py",
         "--out-of-scope", "--duplicate-of", primary],
    ) == cli.EXIT_OK

    # Closing the primary must not retire the out-of-scope sighting with it.
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(run_dir), "--id", primary,
         "--disposition", "fixed", "--note", "fixed the in-scope half"],
    ) == cli.EXIT_BLOCKED

    records = [findings.Finding(**f) for f in state.load_state(run_dir).findings]
    assert findings.unresolved_blocking(records), "the group was retired without authorisation"
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


# =======================================================================
# Round 3: what the second review found (run self-v6, review 02).
# =======================================================================


def test_a_json_object_is_not_a_native_envelope(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Parsing a JSON object is not validating an envelope. Any object with a
    long enough result.summary counted as a completed native review, so
    malformed output could satisfy the completion check and support a
    delivery alongside an adversarial approval."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    impostor = json.dumps(
        {"result": {"summary": "x" * 200}, "rawOutput": "y" * 200}
    )
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(tmp_path, outputs=[_payload("approve")], findings_output=impostor),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED
    saved = state.load_state(run_dir)
    assert not saved.rounds[0].usable
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(run_dir)]) == cli.EXIT_BLOCKED


def test_a_genuine_native_envelope_still_completes(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The legitimate path, so the rule above cannot harden into refusing
    every native review."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("approve")],
            findings_output=_native_payload(
                "I read the range and the archived output. Nothing material here."
            ),
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    assert _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)]) == cli.EXIT_OK


def test_an_inherited_blocking_finding_blocks_delivery(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """The carry-forward is a release condition, not a report."""
    brief = brief_file()
    a = _run_with_open_finding(repo, monkeypatch, brief, tmp_path, tmp_path / "a")
    ancestral = state.load_state(a).findings[0]["id"]
    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)

    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_BLOCKED
    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(b), "--id", ancestral,
         "--disposition", "fixed", "--note", "closed by commit abc1234"],
    ) == cli.EXIT_OK
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_OK


def test_an_inherited_non_blocking_finding_does_not_block(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """Only blocking severities gate. A low-severity item is carried and
    reported, not turned into a barrier."""
    brief = brief_file()
    a = tmp_path / "a"
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(a), "--integration-ref", "main"],
    )
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[
                _payload(
                    "needs-attention",
                    findings_list=[_structured_finding(severity="low", title="a nit")],
                )
            ],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(a)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(a)])
    _run(repo, monkeypatch, ["stop", "--run-dir", str(a), "--reason", "stopped with a nit open"])

    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_OK
    summary = json.loads((b / "delivery.json").read_text(encoding="utf-8"))
    assert [i["severity"] for i in summary["linked_unresolved"]] == ["low"]


def test_resolving_a_current_run_finding_by_origin_uses_the_local_path(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """--origin-run naming THIS run recorded a carried resolution instead of
    dispositioning the finding, so the command reported success while the
    release check still saw it pending."""
    run_dir = tmp_path / "run"
    _start(repo, monkeypatch, brief_file(), run_dir)
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(run_dir)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(run_dir)])
    saved = state.load_state(run_dir)
    own = saved.findings[0]["id"]

    assert _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(run_dir), "--id", own,
         "--origin-run", saved.run_id, "--disposition", "refuted",
         "--note", "checked the caller; cannot occur"],
    ) == cli.EXIT_OK

    reloaded = state.load_state(run_dir)
    assert reloaded.findings[0]["disposition"] == "refuted"
    assert reloaded.carried_resolutions == []
    assert findings.unresolved_blocking([findings.Finding(**f) for f in reloaded.findings]) == []


def test_an_ancestors_duplicate_group_is_read_with_group_semantics(
    repo: Path, monkeypatch, brief_file, tmp_path
):
    """An ancestor's duplicate whose primary was resolved is closed. Reading
    each disposition independently reported it as still open, so a defect
    already dealt with came back as an inherited blocker."""
    brief = brief_file()
    a = tmp_path / "a"
    _run(
        repo, monkeypatch,
        ["start", "--brief", str(brief), "--run-dir", str(a), "--integration-ref", "main"],
    )
    _install_fake_plugin(
        monkeypatch,
        _write_fake_plugin(
            tmp_path,
            outputs=[_payload("needs-attention", findings_list=[_structured_finding()])],
        ),
    )
    _run(repo, monkeypatch, ["gate", "--run-dir", str(a)])
    _run(repo, monkeypatch, ["review", "--run-dir", str(a)])
    primary = state.load_state(a).findings[0]["id"]
    _run(
        repo, monkeypatch,
        ["findings", "record", "--run-dir", str(a), "--round", "1",
         "--channel", runner.CHANNEL_FINDINGS, "--severity", "medium",
         "--title", "the same defect", "--file", "file.txt", "--line", "1",
         "--duplicate-of", primary],
    )
    _run(
        repo, monkeypatch,
        ["findings", "resolve", "--run-dir", str(a), "--id", primary,
         "--disposition", "fixed", "--note", "fixed with a regression"],
    )
    _run(repo, monkeypatch, ["stop", "--run-dir", str(a), "--reason", "done"])

    b = _successor(repo, monkeypatch, brief, tmp_path, tmp_path / "b", a)
    carried = cli._carried_history(state.load_state(b))
    assert carried == [], carried
    assert _run(repo, monkeypatch, ["finish", "--run-dir", str(b)]) == cli.EXIT_OK


def test_the_validator_dependency_is_declared_for_local_installation():
    """The import is unconditional, so a documented local setup that cannot
    install it cannot run the CLI at all. CI pinned it; nothing else did."""
    manifest = REPO_ROOT / "requirements-tooling.txt"
    assert manifest.exists(), "no pinned manifest a local environment can install from"
    text = manifest.read_text(encoding="utf-8")
    assert "jsonschema==4.26.0" in text
    workflow = (REPO_ROOT / ".github/workflows/backend-ci.yml").read_text(encoding="utf-8")
    assert "requirements-tooling.txt" in workflow, "CI must install from the same manifest"
    runbook = (REPO_ROOT / "docs/operations/dev-review-handoff.md").read_text(encoding="utf-8")
    assert "requirements-tooling.txt" in runbook
