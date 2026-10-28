# Split review template

Used by the reviewer agent (fields map to `ReviewProposal`) and rendered by the post-step into the review comment, which starts with `<!-- factory:review -->`. Two halves with different owners: the agent owns the first, the human reads the second.

## Rendered form

```
<!-- factory:review -->
## Review (automated)

### Agent checks
| Check | Status | Detail |
|---|---|---|
| conventions | pass | |
| coverage | fail | src/module.ext:42 new branch has no test |
| secrets | pass | |
| plan_drift | pass | |
| safety | pass | |
| size | pass | 142 lines, 4 files |
| ci | pass | required checks green |
| telemetry | na | |

### Questions for the human
1. Does this belong in module A or in module B? (intent and fit)
2. ...

### Verdict
approve | request_changes | comment | needs_human: one-line reason.
Merge is decided by policy (CI green, threads resolved, `Closes #N`, within limits, plan files, and `autonomy:dark` or a human APPROVED review), not by this review.
```

## Rules for filling it

- Agent checks are objective and verifiable: standards, conventions, test coverage, secrets, plan drift, size, CI. A `fail` names `path:line` and the fix. `na` is allowed only when the check cannot apply (for example `plan_drift` on a human PR).
- Questions for the human are judgment calls a test cannot answer: intent, product fit, "locally correct, globally wrong", simpler alternatives, hidden coupling. 0 to 5 questions, each one sentence. Never restate a failed check as a question.
- No `@mentions`, no secrets, no quoting more than 3 lines of diff.
- The verdict is advisory. The reviewer never merges and never approves on a human's behalf.
