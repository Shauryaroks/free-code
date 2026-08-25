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
    monkeypatch.setattr(orch, "worktree", lambda repo, s: (repo, f"orch/{s['id']}"))
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
