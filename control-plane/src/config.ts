import type { AgentName, Env } from "./types";

/**
 * Per-agent limits mirrored from factory.config.json (the worker cannot read that file).
 * Agents with phases (builder, bug-fixer) are dispatched with an empty `phase` and run all phases,
 * so reserved calls and timeout are the sums of the phase overrides.
 * Keep in sync with factory.config.json#agents.
 */
export const AGENT_SPECS: Record<AgentName, { maxModelCalls: number; timeoutMinutes: number }> = {
  triage: { maxModelCalls: 20, timeoutMinutes: 10 },
  builder: { maxModelCalls: 25 + 15 + 60, timeoutMinutes: 10 + 10 + 40 },
  "bug-fixer": { maxModelCalls: 25 + 15 + 60, timeoutMinutes: 10 + 10 + 40 },
  reviewer: { maxModelCalls: 30, timeoutMinutes: 15 },
  verifier: { maxModelCalls: 10, timeoutMinutes: 5 },
  "incident-responder": { maxModelCalls: 25, timeoutMinutes: 15 },
  "security-triage": { maxModelCalls: 15, timeoutMinutes: 10 },
};

export function agentSpec(agent: string): { maxModelCalls: number; timeoutMinutes: number } {
  return (AGENT_SPECS as Record<string, { maxModelCalls: number; timeoutMinutes: number }>)[agent] ?? {
    maxModelCalls: 30,
    timeoutMinutes: 30,
  };
}

export function dailyQuota(env: Env): number {
  const n = Number(env.DAILY_MODEL_CALLS);
  return Number.isFinite(n) && n > 0 ? n : 400;
}

export function maxOpenFactoryPrs(env: Env): number {
  const n = Number(env.MAX_OPEN_FACTORY_PRS);
  return Number.isFinite(n) && n > 0 ? n : 3;
}
