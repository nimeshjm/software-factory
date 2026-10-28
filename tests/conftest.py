import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "work"
    w.mkdir()
    monkeypatch.setenv("WORKSPACE", str(w))
    return w


@pytest.fixture
def factory_cfg():
    return {"dark_paths": ["test/**", "docs/**", "**/*.md", "src/ui.py"],
            "lit_dependencies": ["badlib", "@scope/*", "pool-*"],
            "limits": {"max_diff_lines": 300, "max_diff_files": 8}}


@pytest.fixture(autouse=True)
def _reset_profile():
    from factory.tools.profile import set_profile

    set_profile(None)
    yield
    set_profile(None)


@pytest.fixture
def set_prof():
    """Install a TargetProfile built from keyword sections for the duration of a test."""
    from factory.schemas.target_profile import TargetProfile
    from factory.tools.profile import set_profile

    def _set(**kw):
        p = TargetProfile.model_validate(kw)
        set_profile(p)
        return p

    return _set
