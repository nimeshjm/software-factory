import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

interface Profile {
  smoke?: {
    version?: { auth_header?: string | null } | null;
    paths?: string[];
    expect?: { status?: number; content_type?: string | null };
  };
}

const base = process.env.TARGET_BASE_URL ?? "";
const token = process.env.SMOKE_AUTH_TOKEN ?? "";
const profilePath = process.env.FACTORY_PROFILE ?? "../inputs/factory.json";
const profile = JSON.parse(readFileSync(profilePath, "utf8")) as Profile;

const smoke = profile.smoke ?? {};
const authHeader = smoke.version?.auth_header ?? "";
const expectStatus = smoke.expect?.status ?? 200;
const expectType = smoke.expect?.content_type ?? null;

for (const path of smoke.paths ?? []) {
  test(`GET ${path}`, async ({ page }) => {
    expect(base, "TARGET_BASE_URL must be set").not.toBe("");
    const origin = new URL(base).origin;
    // Send the auth header to the target origin only, never to third parties, and only when both are set.
    if (authHeader && token) {
      await page.route("**/*", (route) => {
        if (new URL(route.request().url()).origin === origin) {
          return route.continue({ headers: { ...route.request().headers(), [authHeader]: token } });
        }
        return route.continue();
      });
    }
    const consoleErrors: string[] = [];
    const serverErrors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("pageerror", (e) => consoleErrors.push(e.message));
    page.on("response", (r) => { if (r.status() >= 500) serverErrors.push(`${r.status()} ${new URL(r.url()).pathname}`); });

    const res = await page.goto(base + path, { waitUntil: "networkidle" });
    expect(res?.status(), "status").toBe(expectStatus);
    if (expectType) {
      expect(res?.headers()["content-type"] ?? "", "content-type").toContain(expectType);
    }
    expect(consoleErrors, "console errors").toEqual([]);
    expect(serverErrors, "5xx responses").toEqual([]);
  });
}
