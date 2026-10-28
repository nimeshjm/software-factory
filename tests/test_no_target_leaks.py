import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"(?i)fantasy|wrangler|FANTASY_|DASHBOARD_TOKEN|nimeshjm")


def _scan(d: Path) -> list[str]:
    hits = []
    for f in sorted(d.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts or f.suffix in {".pyc"}:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    return hits


def test_factory_has_no_target_leaks():
    assert not _scan(ROOT / "factory"), "\n" + "\n".join(_scan(ROOT / "factory"))


def test_agents_have_no_target_leaks():
    d = ROOT / "agents"
    if not d.is_dir():
        import pytest

        pytest.skip("agents/ missing")
    hits = _scan(d)
    assert not hits, "\n" + "\n".join(hits)
