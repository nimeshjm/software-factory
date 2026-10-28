from factory.policy import author_gate, classify_autonomy, merge_policy, parse_depends_on, slug


def test_classify(factory_cfg):
    assert classify_autonomy(["test/a_test.py", "docs/x.md", "README.md", "src/ui.py"], factory_cfg) == "dark"
    assert classify_autonomy([{"path": "docs/a/b.md"}], factory_cfg) == "dark"
    assert classify_autonomy(["test/a.py", "src/index.py"], factory_cfg) == "lit"
    assert classify_autonomy([], factory_cfg) == "lit"
    assert classify_autonomy(["docs/a.md"], factory_cfg, ["@scope/types"]) == "lit"
    assert classify_autonomy(["docs/a.md"], factory_cfg, ["left-pad"]) == "dark"


def _pr(**kw):
    pr = {"number": 7, "title": "feat: x", "body": "Closes #3", "author": "factory-bot[bot]", "headRefName": "feat/3-x",
          "isDraft": False, "mergeable": "MERGEABLE",
          "files": [{"path": "docs/a.md", "additions": 10, "deletions": 2}],
          "checks": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}],
          "reviews": [], "reviewThreads": [{"id": "1", "isResolved": True}]}
    pr.update(kw)
    return pr


def test_merge_dark_ok(factory_cfg):
    d = merge_policy(_pr(), factory_cfg, ["docs/a.md"], False, "pull_request")
    assert d.merge and d.reasons == ["autonomy:dark"]


def test_merge_blockers(factory_cfg):
    def blocked(pr, plan=("docs/a.md",), dep=False, trig="pull_request"):
        d = merge_policy(pr, factory_cfg, list(plan) if plan is not None else None, dep, trig)
        assert not d.merge
        return " | ".join(d.reasons)

    assert "CI not green" in blocked(_pr(checks=[{"name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}]))
    assert "no CI checks" in blocked(_pr(checks=[]))
    assert "unresolved" in blocked(_pr(reviewThreads=[{"id": "1", "isResolved": False}]))
    assert "Closes" in blocked(_pr(body="nothing"))
    assert "outside plan" in blocked(_pr(), plan=("other.md",))
    assert "no plan file list" in blocked(_pr(), plan=None)
    big = [{"path": f"docs/{i}.md", "additions": 1, "deletions": 0} for i in range(9)]
    assert "too many files" in blocked(_pr(files=big), plan=[f["path"] for f in big])
    assert "diff too large" in blocked(_pr(files=[{"path": "docs/a.md", "additions": 300, "deletions": 1}]))
    assert "draft" in blocked(_pr(isDraft=True))


def test_merge_lit_needs_human(factory_cfg):
    files = [{"path": "src/index.py", "additions": 5, "deletions": 1}]
    assert not merge_policy(_pr(files=files), factory_cfg, ["src/index.py"], False, "pull_request").merge
    approved = _pr(files=files, reviews=[{"author": "maintainer", "state": "APPROVED"}])
    assert merge_policy(approved, factory_cfg, ["src/index.py"], False, "pull_request").merge
    bot = _pr(files=files, reviews=[{"author": "x[bot]", "state": "APPROVED"}])
    assert not merge_policy(bot, factory_cfg, ["src/index.py"], False, "pull_request").merge


def test_dependabot(factory_cfg):
    pr = _pr(title="Bump badlib from 3 to 4", headRefName="dependabot/npm_and_yarn/badlib-4", body="",
             author="dependabot[bot]", files=[{"path": "deps.lock", "additions": 1, "deletions": 1}],
             reviews=[{"author": "maintainer", "state": "APPROVED"}])
    assert "cron trigger" in " ".join(merge_policy(pr, factory_cfg, None, True, "pull_request").reasons)
    assert merge_policy(pr, factory_cfg, None, True, "cron").merge  # human approved, no plan or Closes needed
    factory_cfg2 = {**factory_cfg, "dark_paths": factory_cfg["dark_paths"] + ["deps.lock"]}
    pr2 = {**pr, "reviews": [], "title": "Bump left-pad from 1 to 2"}
    assert merge_policy(pr2, factory_cfg2, None, True, "cron").merge
    pr3 = {**pr, "reviews": []}  # lit dependency, no approval
    assert not merge_policy(pr3, factory_cfg2, None, True, "cron").merge


def test_author_gate():
    assert author_gate("OWNER", []) and author_gate("collaborator", [])
    assert not author_gate("NONE", []) and not author_gate(None, None)
    assert author_gate("NONE", ["factory:accepted"])


def test_slug():
    assert slug("Add /status endpoint (gated)!") == "add-status-endpoint-gated"
    assert slug("   ") == "change" and slug("!!!") == "change"
    assert len(slug("word " * 30)) <= 40 and not slug("word " * 30).endswith("-")


def test_parse_depends_on():
    assert parse_depends_on("x\nDepends on #12, #13 and #12\nfoo #99") == [12, 13]
    assert parse_depends_on("depends on #4\nDepends On #5") == [4, 5]
    assert parse_depends_on(None) == [] and parse_depends_on("see #3") == []


def test_policy_cli_and_validate_cli(tmp_path, capsys):
    import json
    from factory.policy.__main__ import main as policy_main
    from factory.schemas import main as schemas_main
    from factory.guards.output_guard import main as guard_main

    pr = {"number": 1, "title": "Docs", "body": "Closes #1", "author": "app/factory-bot", "headRefName": "feat/1-docs",
          "files": [{"path": "docs/a.md", "additions": 1, "deletions": 0}],
          "checks": [{"name": "ci", "status": "completed", "conclusion": "success"}], "reviewThreads": [], "reviews": []}
    factory = {"dark_paths": ["docs/**"], "lit_dependencies": [], "limits": {"max_diff_lines": 300, "max_diff_files": 8}}
    (tmp_path / "pr.json").write_text(json.dumps(pr))
    (tmp_path / "factory.json").write_text(json.dumps(factory))
    (tmp_path / "plan.json").write_text(json.dumps(["docs/a.md"]))
    assert policy_main(["classify-autonomy", "--pr", str(tmp_path / "pr.json"), "--factory", str(tmp_path / "factory.json")]) == 0
    assert capsys.readouterr().out.strip() == "dark"
    assert policy_main(["merge-policy", "--pr", str(tmp_path / "pr.json"), "--factory", str(tmp_path / "factory.json"),
                        "--plan-files", str(tmp_path / "plan.json"), "--trigger", "webhook"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["merge"] is True, out

    good = {"issues": [], "new_issues": [], "summary": "ok"}
    (tmp_path / "p.json").write_text(json.dumps(good))
    assert schemas_main(["validate", "--agent", "triage", "--phase", "triage", "--file", str(tmp_path / "p.json")]) == 0
    (tmp_path / "bad.json").write_text(json.dumps({"issues": "nope"}))
    assert schemas_main(["validate", "--agent", "triage", "--phase", "triage", "--file", str(tmp_path / "bad.json")]) == 1

    (tmp_path / "clean.txt").write_text("mentions APP_TOKEN by name only")
    assert guard_main(["--file", str(tmp_path / "clean.txt")]) == 0
    (tmp_path / "leak.txt").write_text("hcaik_abc123")
    assert guard_main(["--file", str(tmp_path / "leak.txt")]) == 2


def test_always_lit_paths(factory_cfg):
    cfg = {**factory_cfg, "dark_paths": ["**"]}
    assert classify_autonomy(["docs/a.md"], cfg) == "dark"
    assert classify_autonomy(["factory.yml"], cfg) == "lit"
    assert classify_autonomy(["docs/a.md", "factory.yml"], cfg) == "lit"
    assert classify_autonomy([".github/workflows/x.yml"], cfg) == "lit"
    assert classify_autonomy([{"path": ".github/workflows/x.yml"}], cfg) == "lit"
    assert classify_autonomy(["sub/factory.yml"], cfg) == "dark"


def test_merge_uses_profile_dependency_files(factory_cfg):
    cfg = {**factory_cfg, "toolchain": {"dependency_files": ["*.lock"]}}
    pr = _pr(title="Bump left-pad from 1 to 2", headRefName="dependabot/x", body="", author="dependabot[bot]",
             files=[{"path": "deps.lock", "additions": 1, "deletions": 1}])
    assert not merge_policy(pr, cfg, None, True, "cron").merge  # not dark, no human review
