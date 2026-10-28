import type { Env, RunRow } from "./types";
import { IN_FLIGHT_STATUSES } from "./types";
import { startOfUtcDay } from "./util";

export interface QuotaState {
  usedToday: number;
  reservedInFlight: number;
}

/** Pure: today's usage from run rows (finished today) and reservations from in-flight rows. */
export function computeQuota(
  runs: Pick<RunRow, "status" | "finished_at" | "model_calls" | "reserved_calls">[],
  now: Date,
): QuotaState {
  const dayStart = startOfUtcDay(now);
  let usedToday = 0;
  let reservedInFlight = 0;
  for (const r of runs) {
    if ((IN_FLIGHT_STATUSES as string[]).includes(r.status)) reservedInFlight += r.reserved_calls;
    else if (r.finished_at && r.finished_at >= dayStart) usedToday += r.model_calls;
  }
  return { usedToday, reservedInFlight };
}

export async function loadQuota(env: Env, now: Date): Promise<QuotaState> {
  const { results } = await env.DB.prepare(
    "SELECT status, finished_at, model_calls, reserved_calls FROM runs WHERE status IN ('dispatched','started') OR finished_at >= ?",
  )
    .bind(startOfUtcDay(now))
    .all<RunRow>();
  return computeQuota(results ?? [], now);
}

export async function isPaused(env: Env): Promise<boolean> {
  const r = await env.DB.prepare("SELECT value FROM config WHERE key = 'paused'").first<{ value: string }>();
  return r?.value === "1";
}
