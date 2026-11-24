"""Context files listed instead of inlined are readable by name with read_context, and only those."""
import json

from factory import run as run_mod
from factory.prompts import build_task_message, split_context
from factory.tools.context import read_context, set_context_dir


def _ctx(tmp_path):
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "conventions.md").write_text("# Conventions\nuse tabs")
    (ctx / "product-spec.md").write_text("\n\n# Product spec\n" + "spec " * 500)
    (tmp_path / "run.json").write_text(json.dumps({"run_id": "r"}))
    return ctx


def test_read_context_reads_only_listed_names(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "secret.txt").write_text("nope")
    set_context_dir(ctx)
    try:
        assert read_context(name="conventions.md") == "# Conventions\nuse tabs"
        for bad in ("../secret.txt", "/etc/passwd", "missing.md", "context/conventions.md"):
            assert read_context(name=bad).startswith("error: unknown context file")
    finally:
        set_context_dir(None)


def test_index_lists_what_is_not_inlined(tmp_path):
    _ctx(tmp_path)
    msg = build_task_message(tmp_path, "reviewer", "review", context_globs=["conventions.md"])
    assert '<context file="conventions.md">' in msg and '<context file="product-spec.md">' not in msg
    assert "## Context files not inlined (call `read_context`" in msg
    assert "- `product-spec.md` (2,517 chars): Product spec" in msg
    every = build_task_message(tmp_path, "reviewer", "review")
    assert "Context files not inlined" not in every and '<context file="product-spec.md">' in every
    inlined, listed = split_context(tmp_path, None, ["conventions.md"])
    assert [f.name for f in inlined] == ["conventions.md"] and [f.name for f in listed] == ["product-spec.md"]


def test_inline_context_for():
    ctx = {"inline_context_files": ["conventions.md"], "phase_context_files": {"revise": ["conventions.md", "architecture.md"]}}
    assert run_mod.inline_context_for(ctx, "revise") == ["conventions.md", "architecture.md"]
    assert run_mod.inline_context_for(ctx, "review") == ["conventions.md"]
    assert run_mod.inline_context_for({}, "review") is None
