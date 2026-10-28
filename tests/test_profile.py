import json
import logging
import warnings

import pytest
from pydantic import ValidationError

from factory import config as C
from factory.prompts import PLACEHOLDER, build_task_message, load_system_prompt, render_target_profile
from factory.schemas import ALL_MODELS, dump_schemas
from factory.schemas import main as schemas_main
from factory.schemas.target_profile import TargetProfile


def test_defaults():
    p = TargetProfile()
    assert p.limits.max_diff_lines == 300 and p.limits.max_diff_files == 8
    assert p.context_files == ["AGENTS.md", ".agents/*.md", "docs/product-spec.md"]
    assert p.deploy.workflow_name == "Deploy" and p.deploy.branch is None
    assert p.smoke.version is None and p.smoke.expect.status == 200
    assert p.honeycomb.enabled is False and p.honeycomb.liveness.window_minutes == 120
    assert p.honeycomb.errors.window_minutes == 1440 and p.toolchain.checks == []


def test_extra_forbidden_everywhere():
    for bad in ({"nope": 1}, {"toolchain": {"nope": 1}}, {"smoke": {"expect": {"x": 1}}},
                {"honeycomb": {"errors": {"x": 1}}}, {"toolchain": {"checks": [{"name": "a", "run": "b", "x": 1}]}}):
        with pytest.raises(ValidationError):
            TargetProfile.model_validate(bad)


def test_length_caps():
    with pytest.raises(ValidationError):
        TargetProfile.model_validate({"risk": {"never_do": ["x" * 401]}})
    with pytest.raises(ValidationError):
        TargetProfile.model_validate({"risk": {"never_do": ["x"] * 51}})
    TargetProfile.model_validate({"risk": {"never_do": ["x" * 400] * 50}})


def test_bad_regex_rejected():
    with pytest.raises(ValidationError):
        TargetProfile.model_validate({"protected": {"commands": ["("]}})
    with pytest.raises(ValidationError):
        TargetProfile.model_validate({"secrets": {"patterns": ["["]}})


def test_null_sections_use_defaults():
    p = TargetProfile.model_validate({"toolchain": None, "smoke": {"version": None}})
    assert p.toolchain.checks == []


def test_legacy_version_path_migrates_with_warning():
    with pytest.warns(DeprecationWarning, match="version_path"):
        p = TargetProfile.model_validate({"smoke": {"version_path": "/version", "paths": ["/"]}})
    v = p.smoke.version
    assert (v.path, v.sha_field, v.auth_header) == ("/version", "sha", "x-dashboard-token")
    assert p.smoke.paths == ["/"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        TargetProfile.model_validate(json.loads(p.model_dump_json()))  # normalised form has no legacy key


def test_schema_dump_includes_profile(tmp_path):
    assert "TargetProfile" in ALL_MODELS
    dump_schemas(tmp_path)
    assert (tmp_path / "TargetProfile.schema.json").is_file()


def test_load_profile(tmp_path):
    with pytest.raises(C.ProfileError, match="missing"):
        C.load_profile(tmp_path)
    (tmp_path / "factory.json").write_text("{not json")
    with pytest.raises(C.ProfileError, match="not valid JSON"):
        C.load_profile(tmp_path)
    (tmp_path / "factory.json").write_text('{"limits": {"max_diff_lines": "x"}}')
    with pytest.raises(C.ProfileError, match="max_diff_lines"):
        C.load_profile(tmp_path)
    (tmp_path / "factory.json").write_text('{"dark_paths": ["docs/**"]}')
    assert C.load_profile(tmp_path).dark_paths == ["docs/**"]


def test_factory_config_shape():
    cfg = C.load_config()
    assert set(cfg["honeycomb"]) == {"api_base", "mcp_url"}
    assert cfg["allowed_hosts"] and cfg["factory"]["app_slug"] and cfg["factory"]["ref"]


YAML = """
dark_paths: [docs/**]
toolchain:
  runtime: {python: "3.12"}
  checks:
    - {name: test, run: pytest -q}
smoke:
  version_path: /version
"""


def test_validate_profile_cli_yaml_and_write(tmp_path, capsys):
    f = tmp_path / "factory.yml"
    f.write_text(YAML)
    with pytest.warns(DeprecationWarning):
        assert schemas_main(["validate-profile", "--file", str(f)]) == 0
    assert f.read_text() == YAML  # no --write: untouched
    with pytest.warns(DeprecationWarning):
        assert schemas_main(["validate-profile", "--file", str(f), "--write"]) == 0
    data = json.loads(f.read_text())
    assert data["toolchain"]["checks"] == [{"name": "test", "run": "pytest -q"}]
    assert data["smoke"]["version"] == {"path": "/version", "sha_field": "sha", "auth_header": "x-dashboard-token"}
    assert data["limits"] == {"max_diff_lines": 300, "max_diff_files": 8}
    assert "version_path" not in data["smoke"]
    assert schemas_main(["validate-profile", "--file", str(f), "--write"]) == 0  # idempotent on JSON input
    assert json.loads(f.read_text()) == data


def test_validate_profile_cli_errors(tmp_path, capsys):
    empty = tmp_path / "e.yml"
    empty.write_text("")
    assert schemas_main(["validate-profile", "--file", str(empty)]) == 1
    assert "empty" in capsys.readouterr().err
    bad = tmp_path / "b.yml"
    bad.write_text("toolchain:\n  checks: [{name: a}]\nunknown: 1\n")
    assert schemas_main(["validate-profile", "--file", str(bad), "--write"]) == 1
    err = capsys.readouterr().err
    assert "toolchain.checks.0.run" in err and "unknown" in err
    assert bad.read_text().startswith("toolchain:")  # not rewritten on failure
    assert schemas_main(["validate-profile", "--file", str(tmp_path / "missing.yml")]) == 1


def test_prompt_renders_target_profile(tmp_path, caplog):
    root = tmp_path / "root"
    (root / "agents" / "a").mkdir(parents=True)
    (root / "agents" / "a" / "PROMPT.md").write_text(f"Intro\n\n{PLACEHOLDER}\n\nOutro")
    prof = TargetProfile.model_validate({
        "protected": {"commands": [r"\bdeploy\b"], "env_markers": ["LIVE_MODE"], "paths": ["cfg/*.toml"]},
        "secrets": {"names": ["API_PASSWORD"]},
        "risk": {"high_paths": ["auth/**"], "high_keywords": ["billing"], "priority1_signals": ["data loss"],
                 "never_do": ["rewrite history"]},
        "toolchain": {"checks": [{"name": "lint", "run": "x"}, {"name": "unit", "run": "y"}],
                      "single_test": "pytest {file}"},
        "honeycomb": {"errors": {"group_by": ["error.kind"]}},
        "context_files": ["B.md", "A.md"]})
    text = load_system_prompt("a", "x", root, prof)
    assert PLACEHOLDER not in text and "## Target profile" in text
    for needle in (r"\bdeploy\b", "LIVE_MODE", "cfg/*.toml", "factory.yml", "API_PASSWORD", "auth/**", "billing",
                   "data loss", "rewrite history", "`B.md`, `A.md`", "`lint`, `unit`", "pytest {file}", "error.kind"):
        assert needle in text, needle
    assert text.index("Intro") < text.index("## Target profile") < text.index("Outro")
    assert "none configured" in render_target_profile(TargetProfile())

    (root / "agents" / "a" / "PROMPT.md").write_text("No placeholder here")
    with caplog.at_level(logging.WARNING):
        text = load_system_prompt("a", "x", root, TargetProfile())
    assert text.rstrip().split("\n")[0] == "No placeholder here" and "## Target profile" in text
    assert "lacks" in caplog.text


def test_context_order_from_profile(tmp_path):
    ctx = tmp_path / "context"
    ctx.mkdir()
    for n in ("a.md", "b.md", "c.md"):
        (ctx / n).write_text(n)
    msg = build_task_message(tmp_path, "x", "y", profile=TargetProfile.model_validate({"context_files": ["c.md", "a.md"]}))
    assert msg.index('file="c.md"') < msg.index('file="a.md"') < msg.index('file="b.md"')
