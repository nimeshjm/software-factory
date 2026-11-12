import json

import pytest
from pydantic import ValidationError

from factory.policy import plan_gate
from factory.policy.__main__ import main as policy_main
from factory.schemas.target_profile import Amigos, PlanReview, TargetProfile


def test_defaults():
    p = TargetProfile()
    assert p.limits.max_context_chars == 60000
    assert p.amigos.all() == [] and p.amigos.two_hats() == {}
    assert p.plan_review.hold_on == ["risk:high"]


def test_login_validation():
    a = Amigos(product=["Alice", "alice", "bob-1"], development=["ALICE"], testing=[])
    assert a.product == ["alice", "bob-1"]
    assert a.all() == ["alice", "bob-1"]
    assert a.two_hats() == {"alice": ["product", "development"]}
    for bad in ["-a", "a-", "a--b", "a b", "", "x" * 40, "a_b"]:
        with pytest.raises(ValidationError):
            Amigos(product=[bad])
    with pytest.raises(ValidationError):
        Amigos(testing=[f"u{i}" for i in range(11)])
    assert len(Amigos(testing=[f"u{i}" for i in range(10)]).testing) == 10
    with pytest.raises(ValidationError):
        Amigos(owner=["x"])


def test_two_hats_across_roles():
    a = Amigos(product=["a", "b"], development=["b"], testing=["c", "b", "a"])
    assert a.two_hats() == {"a": ["product", "testing"], "b": ["product", "development", "testing"]}
    assert a.all() == ["a", "b", "c"]


@pytest.mark.parametrize("bad", ["plan:approved", "needs-human", "status:backlog", "", "  "])
def test_hold_on_rejects(bad):
    with pytest.raises(ValidationError):
        PlanReview(hold_on=[bad])


def test_hold_on_limit():
    with pytest.raises(ValidationError):
        PlanReview(hold_on=[f"l{i}" for i in range(21)])
    assert PlanReview(hold_on=[]).hold_on == []


def test_plan_gate_truth_table():
    prof = {"plan_review": {"hold_on": ["risk:high"]}}
    assert plan_gate([], prof, False).hold is False
    d = plan_gate(["risk:high"], prof, False)
    assert d.hold and d.reasons == ["label:risk:high"]
    assert not plan_gate(["risk:high", "plan:approved"], prof, False).hold
    d = plan_gate(["risk:high", "plan:approved"], prof, True)
    assert d.hold and d.reasons == ["needs_human"]
    d = plan_gate(["risk:high"], prof, True)
    assert d.reasons == ["needs_human", "label:risk:high"]
    custom = {"plan_review": {"hold_on": ["size:L"]}}
    assert plan_gate(["size:L"], custom, False).reasons == ["label:size:L"]
    assert not plan_gate(["risk:high"], custom, False).hold
    assert plan_gate(["risk:high"], {}, False).hold  # defaults when plan_review is absent
    assert plan_gate(["size:L"], TargetProfile(plan_review={"hold_on": ["size:L"]}), False).hold


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_plan_gate_cli(tmp_path, capsys):
    factory = _write(tmp_path, "factory.json", {"plan_review": {"hold_on": ["risk:high"]}})
    plan = _write(tmp_path, "proposal.json", {"needs_human": False})
    issue = _write(tmp_path, "issue.json", {"labels": [{"name": "risk:high"}, "feature"]})
    assert policy_main(["plan-gate", "--issue", issue, "--factory", factory, "--plan", plan]) == 0
    assert json.loads(capsys.readouterr().out) == {"hold": True, "reasons": ["label:risk:high"]}
    issue = _write(tmp_path, "issue.json", {"labels": ["risk:high", "plan:approved"]})
    policy_main(["plan-gate", "--issue", issue, "--factory", factory, "--plan", plan])
    assert json.loads(capsys.readouterr().out) == {"hold": False, "reasons": []}
    nh = _write(tmp_path, "nh.json", {"needs_human": True})
    policy_main(["plan-gate", "--issue", str(tmp_path / "missing.json"), "--factory", factory, "--plan", nh])
    assert json.loads(capsys.readouterr().out) == {"hold": True, "reasons": ["needs_human"]}


def test_amigos_cli(tmp_path, capsys):
    factory = _write(tmp_path, "factory.json", {"amigos": {"product": ["Ann"], "development": ["bo", "ann"]}})
    assert policy_main(["amigos", "--factory", factory]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "product": ["ann"], "development": ["bo", "ann"], "testing": [],
        "two_hats": {"ann": ["product", "development"]}, "any": True}
    policy_main(["amigos", "--factory", _write(tmp_path, "empty.json", {})])
    assert json.loads(capsys.readouterr().out)["any"] is False
