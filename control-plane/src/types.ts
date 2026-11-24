export interface Env {
  DB: D1Database;
  // vars
  TARGET_REPO: string;
  FACTORY_REPO: string;
  TARGET_DEFAULT_BRANCH: string;
  DEPLOY_WORKFLOW_NAME: string;
  FACTORY_REF: string;
  DAILY_MODEL_CALLS: string;
  MAX_OPEN_FACTORY_PRS: string;
  HONEYCOMB_DATASET: string;
  /** Daily paid-key budget in USD and the most one run may reserve (factory.config.json billing). */
  DAILY_PAID_CAP_USD: string;
  RUN_PAID_CAP_USD: string;
  // secrets
  FACTORY_APP_ID: string;
  FACTORY_APP_PRIVATE_KEY: string;
  GITHUB_WEBHOOK_SECRET: string;
  FACTORY_TOKEN: string;
  HONEYCOMB_API_KEY?: string;
  ALERT_WEBHOOK_URL?: string;
}

export type AgentName =
  | "triage"
  | "builder"
  | "bug-fixer"
  | "reviewer"
  | "verifier"
  | "incident-responder"
  | "security-triage";

/** Workflow inputs are all strings; empty when not applicable (CONTRACTS.md). */
export interface DispatchInputs {
  run_id: string;
  issue: string;
  pr: string;
  phase: string;
  dry_run: string;
}

export type RunStatus = "dispatched" | "started" | "success" | "failed" | "timeout" | "denied" | "lost";
export const IN_FLIGHT_STATUSES: RunStatus[] = ["dispatched", "started"];

export interface RunRow {
  id: string;
  agent: string;
  phase: string | null;
  subject: string;
  trigger: string | null;
  issue_number: number | null;
  pr_number: number | null;
  dispatched_at: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  stop_reason: string | null;
  /** Clipped error text from the finished report (migration 0002); null when none. */
  error: string | null;
  model_calls: number;
  reserved_calls: number;
  workflow_run_url: string | null;
  /** Estimated paid-key spend reported at finished, and the allowance handed out at started (migration 0003). */
  paid_cost_usd?: number;
  paid_reserved_usd?: number;
}
