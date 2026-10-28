"""output_guard: secret scan and @mention neutralisation. Pure functions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.schemas.target_profile import TargetProfile

ZWSP = "​"

BASELINE_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "honeycomb_key": re.compile(r"hcaik_[A-Za-z0-9]*"),
    "github_app_token": re.compile(r"ghs_[A-Za-z0-9]*"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]*"),
    "github_fine_grained_pat": re.compile(r"github_pat_[A-Za-z0-9_]*"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]*"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
SKIP_KEYS = frozenset({"files", "path", "branch", "signature"})


@dataclass
class ScanResult:
    text: str
    findings: list[str] = field(default_factory=list)  # secret rule names
    mentions_neutralised: int = 0

    @property
    def blocked(self) -> bool:
        return bool(self.findings)


def patterns_for(profile: TargetProfile | None = None) -> dict[str, re.Pattern[str]]:
    """Baseline secret patterns plus the profile's, compiled at call time (the profile can only add)."""
    pats = dict(BASELINE_SECRET_PATTERNS)
    if profile is not None:
        for i, rx in enumerate(profile.secrets.patterns):
            pats[f"profile_secret_{i + 1}"] = re.compile(rx)
    return pats


def scan_text(text: str, neutralise_mentions: bool = True, profile: TargetProfile | None = None) -> ScanResult:
    findings = [name for name, rx in patterns_for(profile).items() if rx.search(text)]
    out, n = (MENTION.subn("@" + ZWSP, text) if neutralise_mentions else (text, 0))
    return ScanResult(out, findings, n)


def redact(text: str, profile: TargetProfile | None = None) -> str:
    for rx in patterns_for(profile).values():
        text = rx.sub("[REDACTED]", text)
    return text


def scan_obj(obj: Any, profile: TargetProfile | None = None,
             skip_keys: frozenset[str] = SKIP_KEYS) -> tuple[Any, list[str]]:
    """Recursively scan strings in a JSON-like object. Mentions are neutralised except under skip_keys."""
    findings: list[str] = []

    def walk(o: Any, neutralise: bool) -> Any:
        if isinstance(o, str):
            r = scan_text(o, neutralise, profile)
            findings.extend(f for f in r.findings if f not in findings)
            return r.text
        if isinstance(o, list):
            return [walk(i, neutralise) for i in o]
        if isinstance(o, dict):
            return {k: walk(v, neutralise and k not in skip_keys) for k, v in o.items()}
        return o

    return walk(obj, True), findings


def main(argv: list[str] | None = None) -> int:
    """CLI: scan files for secrets. Prints {"blocked", "findings"}; exit 2 when blocked."""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="python -m factory.guards.output_guard")
    ap.add_argument("--file", action="append", required=True, help="file to scan (repeatable)")
    ap.add_argument("--profile", help="normalised profile JSON (inputs/factory.json) adding secret patterns")
    ns = ap.parse_args(argv)
    profile = None
    if ns.profile:
        from factory.config import ProfileError, load_profile_file

        try:
            profile = load_profile_file(ns.profile)
        except ProfileError as e:
            sys.stderr.write(f"{e}\n")
            return 1
    findings: list[str] = []
    for f in ns.file:
        text = open(f, encoding="utf-8", errors="replace").read()
        for name in scan_text(text, neutralise_mentions=False, profile=profile).findings:
            tag = f"{name} in {f}"
            if tag not in findings:
                findings.append(tag)
    json.dump({"blocked": bool(findings), "findings": findings}, sys.stdout)
    sys.stdout.write("\n")
    return 2 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
