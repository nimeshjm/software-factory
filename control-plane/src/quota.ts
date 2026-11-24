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

/** Hour (UTC) by which the free tier's daily quota has reset (midnight Pacific: 07:00 PDT, 08:00 PST). */
export const FREE_RESET_UTC_HOUR = 7;

export interface PaidBudget {
  cap: number;
  runCap: number;
  spentToday: number;
  reservedInFlight: number;
  /** Paid key blocked for this UTC month (billing_blocked with a free key; cleared by /admin/resume). */
  blocked: boolean;
  /** A run stopped on paid_budget since the later of today's start and the free tier's last reset. */
  exhausted: boolean;
}

type PaidRow = Pick<RunRow, "id" | "status" | "finished_at" | "stop_reason" | "paid_cost_usd" | "paid_reserved_usd">;

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Start of the window in which a paid_budget stop still means "no free quota and no paid budget left". */
export function exhaustionWindowStart(now: Date): string {
  const reset = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), FREE_RESET_UTC_HOUR));
  const day = startOfUtcDay(now);
  return now >= reset && reset.toISOString() > day ? reset.toISOString() : day;
}

export const utcMonth = (now: Date): string => now.toISOString().slice(0, 7);

/** Pure: today's paid spend and in-flight reservations (excluding `exceptId`), from run rows. */
export function computePaidBudget(rows: PaidRow[], now: Date, cap: number, runCap: number, blocked: boolean, exceptId?: string): PaidBudget {
  const dayStart = startOfUtcDay(now);
  const since = exhaustionWindowStart(now);
  let spentToday = 0;
  let reservedInFlight = 0;
  let exhausted = false;
  for (const r of rows) {
    if (r.id === exceptId) continue;
    if ((IN_FLIGHT_STATUSES as string[]).includes(r.status)) reservedInFlight += num(r.paid_reserved_usd);
    else if (r.finished_at && r.finished_at >= dayStart) {
      spentToday += num(r.paid_cost_usd);
      if (r.stop_reason === "paid_budget" && r.finished_at >= since) exhausted = true;
    }
  }
  return { cap, runCap, spentToday, reservedInFlight, blocked, exhausted };
}

/** Pure: what a starting run may spend on the paid key (USD, 4 decimals). */
export function paidAllowance(b: PaidBudget): number {
  if (b.blocked) return 0;
  const left = Math.max(0, b.cap - b.spentToday - b.reservedInFlight);
  return Math.floor(Math.min(b.runCap, left) * 10_000) / 10_000;
}

/** Pure: dispatching is pointless when free quota and paid budget are both gone (or the paid key is blocked and
 * the free key already ran dry). A run then starts, finds nothing to call, and stops on paid_budget at once. */
export function paidExhausted(b: PaidBudget): boolean {
  return b.exhausted && paidAllowance(b) < 0.005;
}

export async function loadPaidBudget(env: Env, now: Date, cap: number, runCap: number, exceptId?: string): Promise<PaidBudget> {
  const [{ results }, blocked] = await Promise.all([
    env.DB.prepare(
      "SELECT id, status, finished_at, stop_reason, paid_cost_usd, paid_reserved_usd FROM runs WHERE status IN ('dispatched','started') OR finished_at >= ?",
    )
      .bind(startOfUtcDay(now))
      .all<PaidRow>(),
    env.DB.prepare("SELECT value FROM config WHERE key = 'paid_blocked_month'").first<{ value: string }>(),
  ]);
  return computePaidBudget(results ?? [], now, cap, runCap, blocked?.value === utcMonth(now), exceptId);
}

export async function isPaused(env: Env): Promise<boolean> {
  const r = await env.DB.prepare("SELECT value FROM config WHERE key = 'paused'").first<{ value: string }>();
  return r?.value === "1";
}
