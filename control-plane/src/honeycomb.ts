import type { Env } from "./types";

const API = "https://api.eu1.honeycomb.io";

/** Fire-and-forget Honeycomb event. Never throws; no-op without a key. */
export function sendEvent(env: Env, data: Record<string, unknown>, ctx?: ExecutionContext): Promise<void> {
  if (!env.HONEYCOMB_API_KEY) return Promise.resolve();
  const dataset = encodeURIComponent(env.HONEYCOMB_DATASET || "factory");
  const p = fetch(`${API}/1/events/${dataset}`, {
    method: "POST",
    headers: {
      "x-honeycomb-team": env.HONEYCOMB_API_KEY,
      "content-type": "application/json",
      "user-agent": "factory-control-plane",
    },
    body: JSON.stringify({ service: "factory-control-plane", ...data }),
  })
    .then(() => undefined)
    .catch(() => undefined);
  ctx?.waitUntil(p);
  return p;
}
