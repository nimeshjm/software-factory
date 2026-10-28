import type { Env } from "./types";

/** Slack/Discord compatible webhook alert. https only; never throws. */
export function sendAlert(env: Env, text: string, ctx?: ExecutionContext): Promise<void> {
  const url = env.ALERT_WEBHOOK_URL;
  if (!url || !isHttps(url)) return Promise.resolve();
  const body = JSON.stringify({ text, content: text });
  const p = fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "user-agent": "factory-control-plane" },
    body,
  })
    .then(() => undefined)
    .catch(() => undefined);
  ctx?.waitUntil(p);
  return p;
}

export function isHttps(url: string): boolean {
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}
