import dataclasses
import json

from factory import run as run_mod
from factory.run import needs_finalize
from tests.test_e2e import _inputs


def test_needs_finalize():
    assert needs_finalize("end_turn", False, True)
    assert not needs_finalize("end_turn", False, False)
    assert needs_finalize("limit_total_tokens", False, False)
    assert needs_finalize("limit_turns", False, False)
    assert not needs_finalize("end_turn", True, True)
    assert not needs_finalize("limit_turns", True, False)
    assert not needs_finalize("quota_cap", False, True)


def test_limit_turns_gets_finalize(tmp_path, ws, monkeypatch):
    script = [{"tool": "read_file", "input": {"path": "nothing.txt"}}] * 2
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script))
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(sp))
    monkeypatch.setenv("FACTORY_RUN_ID", "r1")
    real = run_mod.resolve_run_spec

    def tight(cfg, agent, phase):
        spec = real(cfg, agent, phase)
        return dataclasses.replace(spec, limits={"turns": 2},
                                   context={**spec.context, "budget_soft_fraction": 5.0})  # no nudge: hit the limit

    monkeypatch.setattr(run_mod, "resolve_run_spec", tight)
    out = tmp_path / "out"
    rc = run_mod.main(["--agent", "triage", "--phase", "triage", "--inputs", str(_inputs(tmp_path)),
                       "--out", str(out), "--dry-run", "--fake-model"])
    res = json.loads((out / "result.json").read_text())
    assert rc == 0, (res["stop_reason"], res["error"], res["guard_events"])
    assert res["success"] and res["stop_reason"] == "end_turn"
    assert any(e["guard"] == "budget_guard" and e["rule"] == "finalize" and e["detail"] == "limit_turns"
               for e in res["guard_events"])
