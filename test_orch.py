import json, orch, pytest


@pytest.fixture(autouse=True)
def _isolate_files(tmp_path, monkeypatch):
    """Never let tests write the real stats/state files."""
    monkeypatch.setattr(orch, "STATS", tmp_path / "stats.json")
    monkeypatch.setattr(orch, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(orch, "STOP", tmp_path / "stop")


def test_escalation_prefers_cheap_then_learns(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "STATS", tmp_path / "s.json")
    monkeypatch.setattr(orch, "available", lambda a: True)
    calls = []

    def fake(agent, prompt, cwd):
        calls.append(agent)
        return True, "", 0.0

    monkeypatch.setattr(orch, "run_agent", fake)
    # gate fails for everyone but claude -> must escalate, not give up
    monkeypatch.setattr(orch, "gate", lambda s, c: (calls[-1] == "claude", "boom"))
    step = {"id": "fe", "task": "frontend", "prompt": "p"}

    assert orch.run_step(step, ".", {"left": 99}) == "claude"
    assert calls[0] != "claude", "must try a free tier before the paid one"
    loser = calls[0]
    # one failure is not enough to abandon a free tier: retrying free is still cheap
    assert orch.cost_per_pass("frontend", loser) < orch.cost_per_pass("frontend", "claude")
    # but sustained failure must flip the order, or we burn quota forever
    for _ in range(8):
        orch.record("frontend", loser, False)
    assert orch.cost_per_pass("frontend", loser) > orch.cost_per_pass("frontend", "claude")
    calls.clear()
    orch.run_step(step, ".", {"left": 99})
    assert loser not in calls, "a proven-bad agent must rank below the paid tier"


def test_budget_blocks_paid_tier(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "STATS", tmp_path / "s.json")
    monkeypatch.setattr(orch, "available", lambda a: True)
    monkeypatch.setattr(orch, "run_agent", lambda a, p, c: (True, "", 0.0))
    monkeypatch.setattr(orch, "gate", lambda s, c: (False, "x"))
    try:
        orch.run_step({"id": "be", "task": "backend", "prompt": "p"}, ".", {"left": 0})
        assert False, "should have exhausted"
    except SystemExit as e:
        assert "failed" in str(e)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))


# --- parallelism ---

def _pipeline(tmp_path, steps, budget=99):
    p = tmp_path / "pipe.json"
    p.write_text(json.dumps({"repo": str(tmp_path), "budget": budget, "steps": steps}))
    return str(p)


def _stub_git(monkeypatch, order=None):
    """No git in tests. Records merge order if given a list."""
    monkeypatch.setattr(orch, "worktree", lambda repo, s: (repo, f"orch/{s['id']}", "base"))
    monkeypatch.setattr(orch, "save_diff", lambda wt, base, sid: None)
    monkeypatch.setattr(orch, "changed_files", lambda wt, base: [])
    monkeypatch.setattr(orch, "cleanup", lambda repo, s, b: None)
    monkeypatch.setattr(orch, "subprocess", type("S", (), {
        "run": staticmethod(lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())}))
    monkeypatch.setattr(orch, "merge_back",
                        lambda repo, s, b: order.append(s["id"]) if order is not None else None)


def test_independent_steps_actually_run_concurrently(tmp_path, monkeypatch):
    """A barrier both steps must reach. Sequential execution deadlocks it."""
    import threading
    _stub_git(monkeypatch)
    barrier = threading.Barrier(2, timeout=5)

    def fake_run_step(step, cwd, budget):
        barrier.wait()          # BrokenBarrierError if the other step never arrives
        return "opencode"

    monkeypatch.setattr(orch, "run_step", fake_run_step)
    steps = [
        {"id": "backend", "task": "backend", "owns": ["src/api"], "prompt": "x"},
        {"id": "frontend", "task": "frontend", "owns": ["src/ui"], "prompt": "y"},
    ]
    orch.main(_pipeline(tmp_path, steps))   # raises BrokenBarrierError if serial


def test_dependent_step_waits_for_its_wave(tmp_path, monkeypatch):
    order = []
    _stub_git(monkeypatch, order)
    monkeypatch.setattr(orch, "run_step", lambda s, c, b: "opencode")
    steps = [
        {"id": "backend", "task": "backend", "owns": ["src/api"], "prompt": "x"},
        {"id": "frontend", "task": "frontend", "owns": ["src/ui"], "prompt": "y"},
        {"id": "qa", "task": "qa", "needs": ["backend", "frontend"], "owns": ["tests"], "prompt": "z"},
    ]
    orch.main(_pipeline(tmp_path, steps))
    assert order.index("qa") == 2, "qa must merge after both deps"


# --- ownership: the thing that makes conflicts structurally impossible ---

def test_concurrent_overlap_is_rejected():
    steps = [
        {"id": "a", "task": "backend", "owns": ["src"], "prompt": "x"},
        {"id": "b", "task": "frontend", "owns": ["src/ui"], "prompt": "y"},   # nested under src
    ]
    try:
        orch.validate_ownership(steps)
        assert False, "nested concurrent ownership must be rejected"
    except SystemExit as e:
        assert "concurrently" in str(e)


def test_ordered_steps_may_own_everything():
    """A review step owning the whole tree is fine -- it never runs beside anything."""
    steps = [
        {"id": "a", "task": "backend", "owns": ["src/api"], "prompt": "x"},
        {"id": "review", "task": "review", "needs": ["a"], "owns": ["."], "prompt": "r"},
    ]
    orch.validate_ownership(steps)          # must not raise


def test_transitive_ordering_counts():
    """review needs qa needs a -- review and a are ordered, not concurrent."""
    steps = [
        {"id": "a", "task": "backend", "owns": ["src/api"], "prompt": "x"},
        {"id": "qa", "task": "qa", "needs": ["a"], "owns": ["tests"], "prompt": "q"},
        {"id": "review", "task": "review", "needs": ["qa"], "owns": ["."], "prompt": "r"},
    ]
    orch.validate_ownership(steps)


def test_cycle_is_caught():
    steps = [
        {"id": "a", "task": "backend", "needs": ["b"], "owns": ["x"], "prompt": "1"},
        {"id": "b", "task": "backend", "needs": ["a"], "owns": ["y"], "prompt": "2"},
    ]
    try:
        orch.validate_ownership(steps)
        assert False, "cycle must be caught"
    except SystemExit as e:
        assert "cycle" in str(e)


def test_shipped_pipeline_is_valid():
    import pathlib
    orch.validate_ownership(json.loads(pathlib.Path("pipeline.json").read_text())["steps"])


# --- real-git tests: merge_back semantics ---

import subprocess


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """Real git repo: one commit with a.py and b.py, then a branch that edits both and adds c.py."""
    r = tmp_path / "repo"; r.mkdir()
    _git(r, "init", "-q", "-b", "master"); _git(r, "config", "user.email", "t@t"); _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("a0\n"); (r / "b.py").write_text("b0\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "init")
    _git(r, "checkout", "-qb", "orch/x")
    (r / "a.py").write_text("a1\n"); (r / "b.py").write_text("b1\n"); (r / "c.py").write_text("c1\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "wip")
    _git(r, "checkout", "-q", "master")
    return r


def test_merge_back_copies_owned_file_and_drops_unowned(repo):
    step = {"id": "x", "task": "backend", "owns": ["a.py"]}
    orch.merge_back(str(repo), step, "orch/x")
    assert (repo / "a.py").read_text() == "a1\n"      # owned: merged
    assert (repo / "b.py").read_text() == "b0\n"      # unowned: untouched
    assert not (repo / "c.py").exists()               # unowned new file: dropped
    assert "x (backend)" in _git(repo, "log", "-1", "--format=%s")


def test_merge_back_propagates_owned_file_deletion(repo):
    _git(repo, "checkout", "-q", "orch/x"); (repo / "a.py").unlink()
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "rm a"); _git(repo, "checkout", "-q", "master")
    orch.merge_back(str(repo), {"id": "x", "task": "backend", "owns": ["a.py"]}, "orch/x")
    assert not (repo / "a.py").exists()


# --- out-of-bounds + change log ---

def test_out_of_bounds_is_files_not_under_any_owned_path():
    step = {"owns": ["boltons/strutils.py", "tests/"]}
    files = ["boltons/strutils.py", "tests/test_strutils.py", "boltons/urlutils.py", "README.md"]
    assert orch.out_of_bounds(step, files) == ["boltons/urlutils.py", "README.md"]


def test_changed_files_and_save_diff(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "RUNS", tmp_path / "runs")
    _git(repo, "checkout", "-q", "orch/x")
    base = _git(repo, "rev-parse", "master").strip()
    assert sorted(orch.changed_files(str(repo), base)) == ["a.py", "b.py", "c.py"]
    p = orch.save_diff(str(repo), base, "x")
    assert p.name == "x.diff" and "+a1" in p.read_text()
