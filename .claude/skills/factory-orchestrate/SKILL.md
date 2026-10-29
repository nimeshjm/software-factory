---
name: factory-orchestrate
description: Run a change to the software factory end to end from the main (Opus) session - research with factory-researcher (Fable), implement in work packages with factory-implementer (Sonnet), verify, update HANDOFF.md. Use for planned work such as HANDOFF.md plans A and B, or any change spanning more than one area of the repo.
---

# Orchestrating factory work

The main session orchestrates and owns the decisions; subagents do the legwork. Run this from an Opus session.

| Role | Who | Does |
|---|---|---|
| Orchestrate | main session (Opus) | scope, contract, work packages, review, verification, handoff |
| Research | `factory-researcher` (Fable) | read-only questions about this repo, the target, external APIs and limits |
| Implement | `factory-implementer` (Sonnet) | one work package each, with tests and checks |

## 1. Orient

Read `HANDOFF.md` and the `CONTRACTS.md` sections the change touches. If the work is a plan in `HANDOFF.md`, start from it rather than redesigning.

## 2. Research (only what is still unknown)

Spawn `factory-researcher` for each independent open question, in parallel in one message. Give each a narrow question and say what is already known. Skip research when the answer is a lookup you can do in a couple of tool calls.

## 3. Contract first

Write the `CONTRACTS.md` changes yourself before any implementation: new fields, files, labels, endpoints, profile sections. The four areas (harness, prompts, scripts and workflows, control plane) are built against it in parallel and only agree because of it.

## 4. Work packages

Split the change into packages with disjoint file sets, usually one per area. Brief each `factory-implementer` with:
- the goal and the `CONTRACTS.md` sections it implements;
- the exact files it owns, and that it must not touch others;
- the acceptance checks (named tests, commands);
- what is already decided, so it does not redesign.

Run independent packages in parallel in one message; run dependent ones in order. Use `isolation: "worktree"` only when packages must touch the same file.

## 5. Review and verify

Read each diff yourself (`git diff`); do not trust a report alone. Then run the full suite in the main session:

```sh
uv run pytest -q
cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run
actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh
```

Send fixes back to the implementer that owns the files (SendMessage keeps its context) rather than starting a fresh one.

## 6. Hand off

Update `HANDOFF.md`: state of the work, verification table, anything left for the owner. Commit and push only when the owner asks.

Owner-only actions, never delegated and never run without the owner asking: `gh workflow run` (including `dry_run=true` graduations), `wrangler deploy`, `gh secret set`, GitHub App permission changes, merging target PRs, anything that reads `.env` values.
