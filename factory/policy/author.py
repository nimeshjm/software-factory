"""Author gating, slugs and dependency parsing."""

from __future__ import annotations

import re

TRUSTED_ASSOCIATIONS = {"OWNER", "COLLABORATOR"}


def author_gate(author_association: str | None, labels: list[str] | None) -> bool:
    """True when the issue/PR may reach full triage or the builders."""
    return (author_association or "").upper() in TRUSTED_ASSOCIATIONS or "factory:accepted" in (labels or [])


def slug(title: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    if len(s) > max_len:
        cut = s[:max_len]
        s = cut[: cut.rfind("-")] if "-" in cut and s[max_len] != "-" else cut
        s = s.strip("-")
    return s or "change"


def parse_depends_on(body: str | None) -> list[int]:
    out: list[int] = []
    for line in (body or "").splitlines():
        m = re.search(r"(?i)\bdepends\s+on\b(.*)", line)
        if m:
            for n in re.findall(r"#(\d+)", m.group(1)):
                if int(n) not in out:
                    out.append(int(n))
    return out
