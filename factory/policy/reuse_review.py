"""Reuse the factory's own review of an unchanged Dependabot head instead of calling the model. Pure."""

from __future__ import annotations

import re
from typing import Any

REVIEW_MARKER = "<!-- factory:review -->"
_VERDICT = re.compile(r"^\*\*Verdict:\*\* (approve|request_changes|comment|needs_human)\s*$", re.MULTILINE)
DEPENDABOT = {"app/dependabot", "dependabot[bot]", "dependabot"}


def reuse_review(pr: dict[str, Any], head_sha: str) -> dict[str, Any] | None:
    """A ReviewProposal carrying the posted verdict, or None when the model must review.

    Only for a Dependabot PR whose latest factory review was posted on `head_sha` and names its verdict:
    apply posts nothing for a head that already has a review, so a new model review would only be thrown
    away while merge policy is re-checked against the same diff.
    """
    if str(pr.get("author") or "") not in DEPENDABOT or not head_sha:
        return None
    prior = pr.get("factory_review") or {}
    body = str(prior.get("body") or "")
    if prior.get("commit_id") != head_sha or REVIEW_MARKER not in body:
        return None
    m = _VERDICT.search(body)
    if not m:
        return None
    return {
        "verdict": m.group(1),
        "summary_md": f"Head {head_sha[:12]} already reviewed; merge policy re-checked without a model call.",
    }
