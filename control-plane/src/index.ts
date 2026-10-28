import { handleFinished, handleStarted, handleStatus } from "./ledger";
import { runTick } from "./scheduler";
import type { Env } from "./types";
import { bearerOk, json } from "./util";
import { handleWebhook } from "./webhooks";

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    try {
      if (path === "/webhooks/github" && req.method === "POST") return await handleWebhook(req, env, ctx);
      if (path === "/status" && req.method === "GET") return await handleStatus(req, env);

      const run = /^\/runs\/([^/]+)\/(started|finished)$/.exec(path);
      if (run && req.method === "POST") {
        if (!(await bearerOk(req, env.FACTORY_TOKEN))) return json({ error: "unauthorized" }, 401);
        const id = decodeURIComponent(run[1]);
        return run[2] === "started" ? await handleStarted(req, env, ctx, id) : await handleFinished(req, env, ctx, id);
      }
      if ((path === "/admin/pause" || path === "/admin/resume") && req.method === "POST") {
        if (!(await bearerOk(req, env.FACTORY_TOKEN))) return json({ error: "unauthorized" }, 401);
        const paused = path === "/admin/pause";
        await env.DB.prepare("INSERT INTO config (key, value) VALUES ('paused', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
          .bind(paused ? "1" : "0")
          .run();
        return json({ paused });
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      console.error("unhandled", e instanceof Error ? e.message : "error");
      return json({ error: "internal" }, 500);
    }
  },

  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    await runTick(env, ctx);
  },
} satisfies ExportedHandler<Env>;
