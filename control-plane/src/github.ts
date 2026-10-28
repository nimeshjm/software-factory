import type { Env } from "./types";

const API = "https://api.github.com";
const UA = "factory-control-plane";

const enc = new TextEncoder();

export function b64url(input: ArrayBuffer | Uint8Array | string): string {
  const bytes = typeof input === "string" ? enc.encode(input) : input instanceof Uint8Array ? input : new Uint8Array(input);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function pemToDer(pem: string): Uint8Array {
  const body = pem
    .replace(/-----BEGIN [A-Z ]+-----/, "")
    .replace(/-----END [A-Z ]+-----/, "")
    .replace(/\\n/g, "")
    .replace(/\s+/g, "");
  const bin = atob(body);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Build an RS256 GitHub App JWT. iat = now-60 (clock drift), exp = iat+540 (< 10 min). Pure apart from WebCrypto. */
export async function buildAppJwt(appId: string, privateKeyPem: string, nowSec: number): Promise<string> {
  const iat = nowSec - 60;
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = b64url(JSON.stringify({ iat, exp: iat + 540, iss: appId }));
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pemToDer(privateKeyPem),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(`${header}.${payload}`));
  return `${header}.${payload}.${b64url(sig)}`;
}

function baseHeaders(token: string): Record<string, string> {
  return {
    authorization: `Bearer ${token}`,
    accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28",
    "user-agent": UA,
  };
}

async function appFetch(env: Env, path: string, init: RequestInit = {}): Promise<Response> {
  const jwt = await buildAppJwt(env.FACTORY_APP_ID, env.FACTORY_APP_PRIVATE_KEY, Math.floor(Date.now() / 1000));
  return fetch(`${API}${path}`, { ...init, headers: { ...baseHeaders(jwt), ...(init.headers ?? {}) } });
}

/** Installation token for the repo, cached in D1 until 5 min before expiry. */
export async function getInstallationToken(env: Env, repo: string): Promise<string> {
  const key = `install:${repo}`;
  const cached = await env.DB.prepare("SELECT token, expires_at FROM tokens WHERE key = ?").bind(key).first<{
    token: string;
    expires_at: string;
  }>();
  if (cached && Date.parse(cached.expires_at) - 5 * 60_000 > Date.now()) return cached.token;

  const instRes = await appFetch(env, `/repos/${repo}/installation`);
  if (!instRes.ok) throw new Error(`installation lookup failed for ${repo}: ${instRes.status}`);
  const inst = (await instRes.json()) as { id: number };
  const tokRes = await appFetch(env, `/app/installations/${inst.id}/access_tokens`, { method: "POST" });
  if (!tokRes.ok) throw new Error(`installation token failed: ${tokRes.status}`);
  const tok = (await tokRes.json()) as { token: string; expires_at: string };
  await env.DB.prepare(
    "INSERT INTO tokens (key, token, expires_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET token = excluded.token, expires_at = excluded.expires_at",
  )
    .bind(key, tok.token, tok.expires_at)
    .run();
  return tok.token;
}

async function ghFetch(env: Env, repo: string, path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getInstallationToken(env, repo);
  return fetch(`${API}${path}`, { ...init, headers: { ...baseHeaders(token), ...(init.headers ?? {}) } });
}

export function nextLink(link: string | null): string | null {
  if (!link) return null;
  for (const part of link.split(",")) {
    const m = /<([^>]+)>;\s*rel="next"/.exec(part);
    if (m) return m[1];
  }
  return null;
}

async function paginate<T>(env: Env, repo: string, firstPath: string, maxPages: number): Promise<T[]> {
  const out: T[] = [];
  let path: string | null = firstPath;
  for (let page = 0; path && page < maxPages; page++) {
    const res: Response = await ghFetch(env, repo, path);
    if (!res.ok) throw new Error(`GitHub ${path} failed: ${res.status}`);
    out.push(...((await res.json()) as T[]));
    const next = nextLink(res.headers.get("link"));
    path = next ? next.replace(API, "") : null;
  }
  return out;
}

export async function dispatchWorkflow(
  env: Env,
  repo: string,
  workflowFile: string,
  inputs: Record<string, string>,
  ref: string,
): Promise<void> {
  const res = await ghFetch(env, repo, `/repos/${repo}/actions/workflows/${workflowFile}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref, inputs }),
  });
  if (res.status !== 204) throw new Error(`workflow_dispatch ${workflowFile} failed: ${res.status}`);
}

export interface GhPR {
  number: number;
  title: string;
  body: string | null;
  author: string;
  headRef: string;
  labels: string[];
  isDraft: boolean;
}

export async function listOpenPRs(env: Env, repo: string, maxPages = 3): Promise<GhPR[]> {
  const raw = await paginate<any>(env, repo, `/repos/${repo}/pulls?state=open&per_page=100`, maxPages);
  return raw.map((p) => ({
    number: p.number,
    title: p.title,
    body: p.body ?? null,
    author: p.user?.login ?? "",
    headRef: p.head?.ref ?? "",
    labels: (p.labels ?? []).map((l: any) => l.name),
    isDraft: !!p.draft,
  }));
}

export interface GhIssue {
  number: number;
  title: string;
  body: string | null;
  labels: string[];
  authorAssociation: string;
  state: string;
}

function mapIssue(i: any): GhIssue {
  return {
    number: i.number,
    title: i.title,
    body: i.body ?? null,
    labels: (i.labels ?? []).map((l: any) => (typeof l === "string" ? l : l.name)),
    authorAssociation: i.author_association ?? "NONE",
    state: i.state,
  };
}

/** Lists issues (pull requests are filtered out). */
export async function listIssues(
  env: Env,
  repo: string,
  opts: { labels?: string[]; state?: "open" | "closed" | "all"; maxPages?: number } = {},
): Promise<GhIssue[]> {
  const q = new URLSearchParams({ state: opts.state ?? "open", per_page: "100" });
  if (opts.labels?.length) q.set("labels", opts.labels.join(","));
  const raw = await paginate<any>(env, repo, `/repos/${repo}/issues?${q}`, opts.maxPages ?? 3);
  return raw.filter((i) => !i.pull_request).map(mapIssue);
}

export async function getIssue(env: Env, repo: string, n: number): Promise<GhIssue> {
  const res = await ghFetch(env, repo, `/repos/${repo}/issues/${n}`);
  if (!res.ok) throw new Error(`getIssue #${n} failed: ${res.status}`);
  return mapIssue(await res.json());
}
