"""Spike findings checks: claimed HTTP statuses must match recorded fetch_url calls; evidence table."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_HEADING = re.compile(r"^#{1,6}\s", re.M)
_URL = re.compile(r"https?://[^\s`'\")\]]+")
_STATUS_LINE = re.compile(r"http\s+status[^\n]*", re.I)
_CODE = re.compile(r"(?<!\d)[1-5]\d\d(?!\d)")
_PLACEHOLDER = re.compile(r"<[^>]*>")
_MENTION = re.compile(r"(^|[^A-Za-z0-9/])@([A-Za-z0-9_])")


def sanitize(text: str) -> str:
    """Neutralise @mentions the way scripts/lib.sh `sanitize` does (zero-width space after `@`)."""
    return _MENTION.sub("\\1@\u200b\\2", text)


def _norm(url: str) -> tuple[str, str, str] | None:
    url = _PLACEHOLDER.sub("", url).rstrip(".,;:*_")
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    if not p.hostname:
        return None
    return (p.scheme.lower(), p.hostname.lower(), p.path.rstrip("/") or "/")


def _sections(md: str) -> list[str]:
    starts = [m.start() for m in _HEADING.finditer(md)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return [md[a:b] for a, b in zip(starts, starts[1:] + [len(md)])]


def _fetches(results: list[dict[str, Any]]) -> list[tuple[tuple[str, str, str], int]]:
    out = []
    for r in results:
        for c in r.get("tool_calls") or []:
            if c.get("name") != "fetch_url" or c.get("http_status") is None:
                continue
            n = _norm(str(c.get("target") or ""))
            if n:
                out.append((n, int(c["http_status"])))
    return out


def check_findings(findings_md: str, results: list[dict[str, Any]]) -> list[str]:
    """Return violations: HTTP statuses claimed in a section with no matching fetch_url call."""
    fetched = _fetches(results)
    violations: list[str] = []
    for sec in _sections(findings_md):
        claimed: list[int] = []
        for line in _STATUS_LINE.findall(sec):
            claimed += [int(c) for c in _CODE.findall(line)]
        if not claimed:
            continue
        heading = sec.strip().splitlines()[0][:80] if sec.strip() else "(section)"
        urls = [n for n in (_norm(u) for u in _URL.findall(sec)) if n]
        for code in dict.fromkeys(claimed):
            if not urls:
                violations.append(f"{heading}: claims HTTP status {code} but names no fetched URL")
            elif not any(code == s and f == u for u in urls for f, s in fetched):
                violations.append(
                    f"{heading}: claims HTTP status {code} but no fetch_url call returned {code} for a URL in this section")
    return violations


def evidence_table(results: list[dict[str, Any]]) -> str:
    """Markdown of every fetch_url and web_search call, in order, grouped by phase."""
    lines: list[str] = []
    for r in results:
        if not r.get("phase"):  # a missing result.json: nothing ran, nothing to show
            continue
        calls = [c for c in (r.get("tool_calls") or []) if c.get("name") in ("fetch_url", "web_search")]
        fetch = [c for c in calls if c["name"] == "fetch_url"]
        search = [c for c in calls if c["name"] == "web_search"]
        lines.append(f"### {r.get('phase') or 'unknown'}")
        lines.append("")
        lines.append("Fetched URLs:")
        if fetch:
            lines += ["", "| URL | HTTP status | Status |", "| --- | --- | --- |"]
            for c in fetch:
                hs = "-" if c.get("http_status") is None else str(c["http_status"])
                url = str(c.get("target") or "").replace("|", "%7C")
                lines.append(f"| {url} | {hs} | {c.get('status', '')} |")
        else:
            lines.append("none")
        lines += ["", "Searches:"]
        lines += [f"- {c.get('target', '')}" for c in search] or ["none"]
        lines.append("")
    return sanitize("\n".join(lines)).rstrip() + "\n"
