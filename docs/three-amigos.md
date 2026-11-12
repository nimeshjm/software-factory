# The three amigos in the factory

Two trios work on every change. The human amigos set direction and constraints; the agent amigos execute
inside them. This page is the written boundary between the two: who owns what, which gate enforces it, and
where each rule lives in code. Change the code and this page together.

## The human amigos

Named per target in `factory.yml` `amigos` (GitHub logins). One person may hold two roles; the profile is
where that is written down, so no role silently defaults to the coding agent. Per issue, the feature form
has an optional "Three amigos for this work" field for overrides; it is for humans and is not parsed.

| Role | Owns | Where it shows |
|---|---|---|
| Product (intent) | the issue and its acceptance criteria, `docs/product-spec.md`, `plan:approved` | plan comment footer; `(product)` questions in reviews |
| Development (direction) | `.agents/*.md`, `factory.yml`, conventions, the shape of prompts; approves `autonomy:lit` PRs | plan comment footer; `(development)` questions |
| Testing (coherence) | the "Questions for the human" half of every review, the smoke checks, `risk.never_do` | review footer; `(testing)` questions |

## The agent amigos

| Role | Agent and phase | Produces |
|---|---|---|
| Architect (plans) | triage; builder and bug-fixer `research` and `plan` | labelled backlog; `research.md`; the plan comment |
| Builder (executes) | builder and bug-fixer `implement` | a branch, commits and a PR inside the plan's file list |
| Reviewer (critiques) | reviewer; verifier and incident-responder on the production side | the split review; deploy verification; bug issues |

Agents propose, pipelines act: no agent holds a GitHub token, and every label, comment, PR and merge is made
by `scripts/apply-proposal.sh` from a schema-validated proposal.

## The gates, in order

1. **Intent before work.** An issue enters the backlog only with acceptance criteria an agent can check and
   a risk label; a feature with no `risk:*` label is never dispatched (`awaiting-risk` in the control plane).
   Issues from untrusted authors wait for `factory:accepted`.
2. **The plan is visible before code.** Every builder and bug-fixer run posts the plan as an issue comment
   before implementing. The pipeline prepends a `Plan at a glance` table (steps, files, whether a human is
   needed, the gate) rendered from the plan's structured fields, so a human can judge it in a minute. An
   issue carrying a label in `plan_review.hold_on` (`risk:high` by default; add `size:L` to see more plans)
   stops there as `status:plan-review` until a human adds `plan:approved`; a plan whose `needs_human` is true
   always stops. The control plane resumes `status:plan-review` + `plan:approved` issues with `implement`
   only. Code: `factory.policy.plan_gate`, `scripts/run-phases.sh`, `apply_plan`, `control-plane/src/preflight.ts`.
3. **Deterministic sensors before any reader.** Size limits, the plan's file list, forbidden commands and
   paths, secret scanning and the target's own checks run in code (`factory/guards`, `factory/policy`, CI).
   Standards live where the agent reads them (`AGENTS.md`, `.agents/*.md`, `docs/product-spec.md`) and are
   inlined into every prompt, so they are kept small: a run stops before any model call when they exceed
   `limits.max_context_chars` (`context_oversize`), and `python -m factory.schemas check-context` runs the
   same check in CI.
4. **The review is split.** The reviewer agent fills `agent_checks` (conventions, coverage, secrets, plan
   drift, safety, size, CI, telemetry) with pass/fail and a `path:line`, and writes 0 to 5
   `questions_for_human`, each tagged with the human amigo it is for. The human reads the questions and the
   shape, not every line. Template: `agents/_shared/review-template.md`.
5. **A human merges what the agent cannot.** A PR merges without a human only when it is `autonomy:dark`
   (confined to `dark_paths`, or a dependency-only bump with `dark_dependency_bumps`). Everything else needs
   an `APPROVED` review from a named amigo when the profile names any, from any human otherwise
   (`factory.policy.merge_policy`). The reviewer agent never approves on a human's behalf.
6. **Production closes the loop.** The verifier checks the deploy and the smoke paths; the incident
   responder and security triage turn signals into issues, which re-enter at gate 1.

## What each half may not do

- The agent half never decides intent: it does not change acceptance criteria, `factory.yml`, `.agents/*`,
  `.github/**` or protected paths, never approves a PR, never adds `plan:approved`, `factory:accepted` or an
  `autonomy:*` label (`HUMAN_ONLY_LABELS` in `scripts/lib.sh`).
- The human half does not re-run what a sensor answers: a failed agent check is fixed by the builder or
  by a `[ci-fix]` commit, not debated in review. Questions that a test can answer are not questions for the
  human (reviewer prompt, step 4).

## Two failure modes this guards against

- **Complacency**: two agents agreeing with each other. The merge policy is code, the human half of the
  review is mandatory for lit changes, and the plan is read before any non-trivial run.
- **Rubber stamping**: a human waving through a large diff. Diffs are capped (300 lines, 8 files), the
  agent checks arrive first, and the questions name what needs judgment.

Source: "The new 3 amigos", https://nimeshjm.com/blog/ai-new-3-amigos-agent-orchestration/.
