export type DashboardUi = "new" | "old";

export const DEFAULT_UI: DashboardUi = "old";

/** localStorage / sessionStorage key prefix for the page's per-viewer settings. */
export const STORAGE_PREFIX = "factory-dash:";
export const AUTO_REFRESH_SECONDS = 60;

export function resolveUi(raw: string | null): DashboardUi {
  if (raw === "old") return "old";
  if (raw === "new") return "new";
  return DEFAULT_UI;
}

export function dashboardHref(hours: number, ui: DashboardUi): string {
  return `?hours=${hours}&ui=${ui}`;
}

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export function fmtMin(min: number): string {
  if (min < 60) return `${min} min`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} h ${min % 60} min`;
  return `${Math.floor(min / 1440)} d`;
}

export function ago(iso: string | null, now: Date): string {
  if (!iso) return "";
  const min = Math.round((now.getTime() - Date.parse(iso)) / 60_000);
  if (!Number.isFinite(min)) return "";
  if (min < -1) return `in ${fmtMin(-min)}`;
  if (min < 1) return "now";
  return `${fmtMin(min)} ago`;
}

export function clock(iso: string | null): string {
  return iso ? iso.slice(5, 16).replace("T", " ") : "";
}

export function issueUrl(repo: string, n: number): string {
  return `https://github.com/${repo}/issues/${n}`;
}

export function prUrl(repo: string, n: number): string {
  return `https://github.com/${repo}/pull/${n}`;
}

export function link(url: string | null, text: string): string {
  return url ? `<a href="${esc(url)}">${esc(text)}</a>` : esc(text);
}

export function subjectLink(repo: string, subject: string, issue: number | null, pr: number | null): string {
  const m = /^(issue|pr):(\d+)$/.exec(subject);
  if (m) return link(m[1] === "pr" ? prUrl(repo, Number(m[2])) : issueUrl(repo, Number(m[2])), subject);
  const extra = pr ? ` ${link(prUrl(repo, pr), `pr:${pr}`)}` : issue ? ` ${link(issueUrl(repo, issue), `issue:${issue}`)}` : "";
  return esc(subject) + extra;
}

/** A long title, cut to one line with the full text on hover. */
export const trunc = (s: string): string => `<span class="trunc" title="${esc(s)}">${esc(s)}</span>`;

/** UTC clock time with the relative time beside it. */
export const when = (iso: string | null, now: Date): string =>
  iso ? `<time datetime="${esc(iso)}">${clock(iso)}</time> <span class="dim">(${ago(iso, now)})</span>` : "";

export const tokens = (t: Record<string, string>): string =>
  Object.entries(t)
    .map(([k, v]) => `--${k}:${v}`)
    .join(";");
