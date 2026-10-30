"""Pydantic models for agent proposals and run results (see CONTRACTS.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- triage ---------------------------------------------------------------
class TriageIssueUpdate(_Base):
    number: int
    labels_add: list[str] = Field(default_factory=list)
    labels_remove: list[str] = Field(default_factory=list)
    comment: str | None = None


class TriageNewIssue(_Base):
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    parent: int | None = None


class TriageProposal(_Base):
    """Triage result: label changes, comments and any child issues to create."""

    issues: list[TriageIssueUpdate] = Field(default_factory=list)
    new_issues: list[TriageNewIssue] = Field(default_factory=list)
    summary: str


# --- builder / bug-fixer phases -------------------------------------------
class ResearchArtifact(_Base):
    """Research phase output: relevant files, reusable utilities, flagged callers."""

    research_md: str


class PlanArtifact(_Base):
    """Plan phase output: implementation plan with files, steps and risks."""

    plan_md: str
    files: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    needs_human: bool = False


class BuildProposal(_Base):
    """Implement phase output: what was committed on the branch."""

    branch: str
    title: str
    body: str
    files: list[str] = Field(default_factory=list)
    checks_passed: bool
    notes: str = ""
    findings_md: str | None = None


# --- reviewer --------------------------------------------------------------
class AgentCheck(_Base):
    name: str
    status: Literal["pass", "fail", "na"]
    detail: str = ""


class InlineComment(_Base):
    path: str
    line: int
    body: str


class CiFix(_Base):
    attempted: bool = False
    commit_message: str | None = None


class ReviewProposal(_Base):
    """Review result in the split template: agent checks plus questions for the human."""

    verdict: Literal["approve", "request_changes", "comment", "needs_human"]
    agent_checks: list[AgentCheck] = Field(default_factory=list)
    questions_for_human: list[str] = Field(default_factory=list)
    inline_comments: list[InlineComment] = Field(default_factory=list)
    summary_md: str
    ci_fix: CiFix = Field(default_factory=CiFix)


# --- verifier --------------------------------------------------------------
class VerifyBug(_Base):
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)


class VerifyProposal(_Base):
    """Post-deploy verification result."""

    outcome: Literal["pass", "fail", "skipped"]
    evidence_md: str
    bug: VerifyBug | None = None
    pr_comment: str | None = None


# --- incident responder ------------------------------------------------------
class Incident(_Base):
    signature: str
    title: str
    body: str
    priority: Literal[1, 2, 3]
    labels: list[str] = Field(default_factory=list)
    existing_issue: int | None = None


class IncidentProposal(_Base):
    """Incident responder result: deduped incidents grouped by error signature."""

    incidents: list[Incident] = Field(default_factory=list)
    summary: str


# --- security triage ---------------------------------------------------------
class SecurityFinding(_Base):
    source: Literal["code_scanning", "dependabot", "secret_scanning"]
    signature: str
    alert_numbers: list[int]
    title: str
    body: str
    priority: Literal[1, 2, 3]
    labels: list[str] = Field(default_factory=list)
    existing_issue: int | None = None


class SecurityResolved(_Base):
    issue: int
    signature: str
    note: str


class SecurityProposal(_Base):
    """Security triage result: one finding per alert signature, plus tracked issues whose alert is gone."""

    findings: list[SecurityFinding] = Field(default_factory=list)
    resolved: list[SecurityResolved] = Field(default_factory=list)
    summary: str


# --- run result ------------------------------------------------------------
class Usage(_Base):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class GuardEvent(_Base):
    guard: str
    rule: str
    detail: str = ""
    ts: str


class ToolCall(_Base):
    name: str
    target: str = ""
    status: Literal["ok", "error", "blocked"]
    http_status: int | None = None
    ts: str


class RunResult(_Base):
    run_id: str
    agent: str
    phase: str
    success: bool
    stop_reason: str
    model_calls: int = 0
    usage: Usage = Field(default_factory=Usage)
    cycles: int = 0
    duration_s: float = 0.0
    guard_events: list[GuardEvent] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    throttles: int = 0
    model_ids_used: list[str] = Field(default_factory=list)
    error: str | None = None
    dry_run: bool = False
