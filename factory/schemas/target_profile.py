"""Target profile: the normalised form of ``factory.yml`` in the target repo (see CONTRACTS.md).

Pydantic owns every default. All strings are capped at 400 chars and lists at 50 items so a profile
cannot flood a prompt.
"""

from __future__ import annotations

import re
import warnings
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Str = Annotated[str, Field(max_length=400)]
StrList = Annotated[list[Str], Field(max_length=50)]


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(_P):
    max_diff_lines: int = Field(300, ge=1)
    max_diff_files: int = Field(8, ge=1)
    max_context_chars: int = Field(60000, ge=1000)


class Runtime(_P):
    node: Str | None = None
    python: Str | None = None
    go: Str | None = None


class Check(_P):
    name: Str
    run: Str


class Toolchain(_P):
    runtime: Runtime = Field(default_factory=Runtime)
    setup: StrList = Field(default_factory=list)
    checks: Annotated[list[Check], Field(max_length=50)] = Field(default_factory=list)
    format: Str | None = None
    single_test: Str | None = None
    dependency_files: StrList = Field(default_factory=list)
    ignore_dirs: StrList = Field(default_factory=list)
    env_prefixes: StrList = Field(default_factory=list)


def _compilable(patterns: list[str]) -> list[str]:
    for rx in patterns:
        try:
            re.compile(rx)
        except re.error as e:
            raise ValueError(f"invalid regex {rx!r}: {e}") from e
    return patterns


class Protected(_P):
    commands: StrList = Field(default_factory=list)
    env_markers: StrList = Field(default_factory=list)
    paths: StrList = Field(default_factory=list)

    _check_commands = field_validator("commands")(_compilable)


class Secrets(_P):
    patterns: StrList = Field(default_factory=list)
    names: StrList = Field(default_factory=list)

    _check_patterns = field_validator("patterns")(_compilable)


class Risk(_P):
    high_paths: StrList = Field(default_factory=list)
    high_keywords: StrList = Field(default_factory=list)
    priority1_signals: StrList = Field(default_factory=list)
    never_do: StrList = Field(default_factory=list)


class SmokeVersion(_P):
    path: Str
    sha_field: Str = "sha"
    auth_header: Str | None = None


class SmokeExpect(_P):
    status: int = 200
    content_type: Str | None = None


class Smoke(_P):
    version: SmokeVersion | None = None
    paths: StrList = Field(default_factory=list)
    expect: SmokeExpect = Field(default_factory=SmokeExpect)


class Deploy(_P):
    workflow_name: Str = "Deploy"
    branch: Str | None = None


class Liveness(_P):
    filters: Annotated[list[dict[str, Any]], Field(max_length=50)] = Field(default_factory=list)
    window_minutes: int = Field(120, ge=1)
    min_events: int = Field(1, ge=0)


class Errors(_P):
    filters: Annotated[list[dict[str, Any]], Field(max_length=50)] = Field(default_factory=list)
    window_minutes: int = Field(1440, ge=1)
    group_by: StrList = Field(default_factory=list)
    fallback_group_by: StrList = Field(default_factory=list)


class HoneycombProfile(_P):
    enabled: bool = False
    dataset: Str | None = None
    environment: Str | None = None
    liveness: Liveness = Field(default_factory=Liveness)
    errors: Errors = Field(default_factory=Errors)


_HOST_RE = re.compile(r"^(?:\*\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")


def _hostnames(hosts: list[str]) -> list[str]:
    if "*" in hosts and len(hosts) > 1:
        raise ValueError("'*' (any public host) must be the only entry in web.allowed_hosts")
    for h in hosts:
        if h == "*":
            continue
        if not _HOST_RE.match(h):
            raise ValueError(f"invalid host {h!r}: use a lowercase hostname or '*.suffix' (no scheme, port or path)")
        if h.startswith("*.") and "." not in h[2:]:
            raise ValueError(f"invalid host {h!r}: wildcard needs a registrable suffix like '*.example.com'")
    return hosts


class Web(_P):
    allowed_hosts: StrList = Field(default_factory=list)

    _check_hosts = field_validator("allowed_hosts")(_hostnames)


_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$")
ROLES = ("product", "development", "testing")


def _logins(logins: list[str]) -> list[str]:
    out: list[str] = []
    for raw in logins:
        login = raw.strip().lower().lstrip("@")
        if not _LOGIN_RE.match(login):
            raise ValueError(f"invalid GitHub login {raw!r}")
        if login not in out:
            out.append(login)
    return out


class Amigos(_P):
    product: Annotated[list[Str], Field(max_length=10)] = Field(default_factory=list)
    development: Annotated[list[Str], Field(max_length=10)] = Field(default_factory=list)
    testing: Annotated[list[Str], Field(max_length=10)] = Field(default_factory=list)

    _check_logins = field_validator("product", "development", "testing", mode="after")(_logins)

    def all(self) -> list[str]:
        """Distinct logins in role order."""
        out: list[str] = []
        for role in ROLES:
            out += [x for x in getattr(self, role) if x not in out]
        return out

    def two_hats(self) -> dict[str, list[str]]:
        """Logins named in two or more roles, with those roles."""
        roles = {x: [r for r in ROLES if x in getattr(self, r)] for x in self.all()}
        return {x: rs for x, rs in roles.items() if len(rs) > 1}


_FORBIDDEN_HOLD = {"plan:approved", "needs-human"}


class PlanReview(_P):
    hold_on: Annotated[list[Str], Field(max_length=20)] = Field(default_factory=lambda: ["risk:high"])

    @field_validator("hold_on")
    @classmethod
    def _check_hold_on(cls, labels: list[str]) -> list[str]:
        for x in labels:
            if not x.strip():
                raise ValueError("hold_on entries must be non-empty label names")
            if x in _FORBIDDEN_HOLD or x.startswith("status:"):
                raise ValueError(f"{x!r} cannot be a plan_review.hold_on label")
        return labels


class TargetProfile(_P):
    dark_paths: StrList = Field(default_factory=list)
    lit_dependencies: StrList = Field(default_factory=list)
    dark_dependency_bumps: bool = False
    limits: Limits = Field(default_factory=Limits)
    toolchain: Toolchain = Field(default_factory=Toolchain)
    protected: Protected = Field(default_factory=Protected)
    secrets: Secrets = Field(default_factory=Secrets)
    context_files: StrList = Field(default_factory=lambda: ["AGENTS.md", ".agents/*.md", "docs/product-spec.md"])
    risk: Risk = Field(default_factory=Risk)
    smoke: Smoke = Field(default_factory=Smoke)
    deploy: Deploy = Field(default_factory=Deploy)
    honeycomb: HoneycombProfile = Field(default_factory=HoneycombProfile)
    web: Web = Field(default_factory=Web)
    amigos: Amigos = Field(default_factory=Amigos)
    plan_review: PlanReview = Field(default_factory=PlanReview)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # An empty YAML section (``toolchain:``) parses as null; treat it as "use defaults".
        data = {k: v for k, v in data.items() if v is not None}
        smoke = data.get("smoke")
        if isinstance(smoke, dict) and "version_path" in smoke:
            smoke = dict(smoke)
            legacy = smoke.pop("version_path")
            if legacy and not smoke.get("version"):
                smoke["version"] = {"path": legacy, "sha_field": "sha", "auth_header": "x-dashboard-token"}
            warnings.warn(
                "smoke.version_path is deprecated; use smoke.version {path, sha_field, auth_header}",
                DeprecationWarning,
                stacklevel=2,
            )
            data["smoke"] = smoke
        return data
