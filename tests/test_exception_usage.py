"""A run that dies on an exception still reports the tokens its finished model calls spent."""
import json

from factory import run as run_mod
from factory.fake_model import FakeModel
from tests.test_e2e import _inputs


def test_exception_keeps_usage_of_finished_calls(tmp_path, ws, monkeypatch):
    script = [{"tool": "read_file", "input": {"path": "nothing.txt"}}] * 2
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script))
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(sp))
    monkeypatch.setenv("FACTORY_RUN_ID", "r1")
    real_stream = FakeModel.stream

    async def dies_on_third(self, *a, **k):
        if self.calls >= 2:
            raise RuntimeError("quota gone")
        async for event in real_stream(self, *a, **k):
            yield event

    monkeypatch.setattr(FakeModel, "stream", dies_on_third)
    out = tmp_path / "out"
    rc = run_mod.main(["--agent", "triage", "--phase", "triage", "--inputs", str(_inputs(tmp_path)),
                       "--out", str(out), "--dry-run", "--fake-model"])
    res = json.loads((out / "result.json").read_text())
    assert rc == 1
    assert res["stop_reason"] == "exception" and "quota gone" in res["error"]
    assert res["usage"] == {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


def test_billing_error_is_billing_blocked(tmp_path, ws, monkeypatch):
    from factory.gemini import ModelBillingError

    monkeypatch.setenv("FACTORY_RUN_ID", "r2")

    async def no_money(self, *a, **k):
        raise ModelBillingError("Gemini billing blocked (prepay balance or spend cap): 402")
        yield  # pragma: no cover

    monkeypatch.setattr(FakeModel, "stream", no_money)
    out = tmp_path / "out"
    rc = run_mod.main(["--agent", "triage", "--phase", "triage", "--inputs", str(_inputs(tmp_path)),
                       "--out", str(out), "--dry-run", "--fake-model"])
    res = json.loads((out / "result.json").read_text())
    assert rc == 1 and res["stop_reason"] == "billing_blocked" and "billing blocked" in res["error"]
