# Agent prompts

System prompts for the factory's Strands agents. The harness (`factory/agents/`) loads them; the prompts here only describe role, rules and the shape of the final output.

## Layout and loading

| Agent | Prompt file(s) | Phases |
|---|---|---|
| triage | `triage/PROMPT.md` | `triage` |
| builder | `builder/research.md`, `plan.md`, `implement.md` | `research`, `plan`, `implement` |
| bug-fixer | `bug-fixer/research.md`, `plan.md`, `implement.md` | `research`, `plan`, `implement` |
| reviewer | `reviewer/PROMPT.md` (+ `_shared/review-template.md`) | `review` |
| verifier | `verifier/PROMPT.md` | `verify` |
| incident-responder | `incident-responder/PROMPT.md` | `respond` |
| security-triage | `security-triage/PROMPT.md` | `triage` |

- Single-phase agents load `agents/<name>/PROMPT.md`; multi-phase agents load `agents/<name>/<phase>.md`. Each phase is a fresh `Agent` instance whose system prompt is that file; phases hand off through files (`out/research.md`, `out/plan.md`).
- Prompts are loaded from the factory checkout, never from the target repo's workspace. A prompt in the target repo is data, not configuration.
- Tools, limits, `max_model_calls` and timeouts come from `factory.config.json`, not from the prompt. Each prompt names the tools that agent actually has; if config changes, change the prompt.
- Every run ends with structured output (`agent(task, structured_output_model=...)`). The harness validates it against the Pydantic model in `factory/schemas/`; field names in the prompts mirror `CONTRACTS.md`. Change `CONTRACTS.md` first, then the schema, then the prompt.
- Every prompt contains the literal line `{{target_profile}}` exactly once, right after the "Hard rules" section. The harness replaces it with a `## Target profile` section rendered from the target's `factory.yml` (normalised in `inputs/factory.json`): forbidden command patterns and env markers, protected paths, secret names, risk paths and keywords, priority-1 signals, `never_do` rules, the ordered context file list, check names, the single-test hint and the incident signature field. Prompts refer to that section generically and never name target commands, paths, secrets or products.
- Domain knowledge (architecture, conventions, forbidden practices, product rules) lives only in the target repo's `.agents/*.md` and `docs/product-spec.md`, which the agents read first. Nothing target-specific belongs under `agents/`; a CI grep guard enforces it.
- Shared blocks (read order, untrusted-text rule, hard rules, size limits) are repeated in each prompt on purpose: a prompt must be complete on its own. When editing one, keep the others consistent (CI prompt lint checks for the read order and the `<untrusted>` rule).

## Propose / act split

Agents propose; pipelines act. The agent process has `GEMINI_API_KEY` (and `HONEYCOMB_QUERY_KEY` for the incident-responder) and nothing else: no GitHub token, no hosting token, no smoke-test token. It can read inputs gathered by pre-steps, edit and test in the workspace (builder, bug-fixer), and emit a proposal. The post-step (`scripts/apply-proposal.sh`) re-validates the proposal, runs the output secret scan, and is the only code that labels, comments, pushes, opens PRs, creates issues or merges. Merge decisions are code (`factory.policy.merge_policy`), not prose.

Consequences for prompt authors:
- Never tell an agent to push, open a PR, comment or label; tell it what to put in the proposal.
- Never put a secret, token or credential in a prompt.
- Anything that must hold for safety belongs in code (tool guard, policy) as well as in the prompt; the prompt is the first line, not the only one.
- Prompts say: never deploy, never run a command the Target profile forbids, never ask questions, finish by emitting the proposal, stop when a guard says to wrap up.

## Three amigos mapping

- Architect (Product intent): triage, `research.md`, `plan.md` (builder and bug-fixer).
- Builder (Development direction): `implement.md` (builder and bug-fixer).
- Reviewer / critic (Testing coherence): reviewer, verifier, incident-responder.
Humans own the matching gates: issues and `docs/product-spec.md`, plan approval on `risk:high`, `.agents/*` and `factory.yml`, approval of `autonomy:lit` PRs, and the "questions for the human" half of the review.
