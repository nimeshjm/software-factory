import type { Blocker, DashboardDoc } from "./dashboard";
import { queueHint } from "./dashboard";
import {
  ago,
  AUTO_REFRESH_SECONDS,
  clock,
  dashboardHref,
  esc,
  link,
  money,
  STORAGE_PREFIX,
  subjectLink,
  tokens,
  trunc,
  when,
} from "./dashboard-shared";

function pill(status: string): string {
  const cls = ["success"].includes(status) ? "good" : ["failed", "timeout", "lost"].includes(status) ? "bad" : ["dispatched", "started", "in_flight"].includes(status) ? "warn" : "mute";
  const icon = cls === "good" ? "&#10003;" : cls === "bad" ? "&#10007;" : cls === "warn" ? "&#8230;" : "&#8211;";
  return `<span class="pill ${cls}">${icon} ${esc(status)}</span>`;
}

/** `num` lists the column indexes that hold numbers: right-aligned, tabular figures. */
const table = (head: string[], rows: string[][], num: number[] = []) => {
  const cls = (i: number) => (num.includes(i) ? ' class="num"' : "");
  return rows.length
    ? `<table><thead><tr>${head.map((h, i) => `<th${cls(i)}>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${cls(i)}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    : `<p class="empty">none</p>`;
};

/** A collapsible section: native <details>, so it works without JavaScript; the inline script remembers its state by `id`. */
function panel(id: string, level: 2 | 3, title: string, count: number | null, open: boolean, body: string): string {
  const n = count === null ? "" : ` <span class="count">(${count})</span>`;
  return `<details class="panel p${level}" id="${id}"${open ? " open" : ""}><summary><h${level}>${title}${n}</h${level}></summary><div class="pbody">${body}</div></details>`;
}

/** Colour tokens per theme. Text tokens (fg, fg2, accent and the four status colours) pass WCAG AA (4.5:1) on bg and card; a test checks it. */
export const THEMES = {
  light: { bg: "#fcfcfb", fg: "#0b0b0b", fg2: "#52514e", line: "#dedcd6", card: "#f4f3ef", accent: "#1f63b8", good: "#137a13", warn: "#8a5a00", bad: "#b42a2a", mute: "#696864" },
  dark: { bg: "#1a1a19", fg: "#f0efec", fg2: "#c3c2b7", line: "#3a3936", card: "#242422", accent: "#6aa6ef", good: "#3fbf3f", warn: "#fab219", bad: "#f07070", mute: "#a3a29c" },
} as const;

/** Runs before first paint so a stored light/dark choice does not flash, and syncs/redirects the preferred UI version. */
const THEME_BOOT = `<script>(function(){var R=document.documentElement;try{var t=localStorage.getItem("${STORAGE_PREFIX}theme");if(t==="light"||t==="dark")R.setAttribute("data-theme",t)}catch(e){}var p=new URLSearchParams(location.search),u=p.get("ui");if(u==="old"){try{localStorage.setItem("${STORAGE_PREFIX}ui","old")}catch(e){}}else if(!u){var s=null;try{s=localStorage.getItem("${STORAGE_PREFIX}ui")}catch(e){}if(s==="new"){p.set("ui","new");location.replace("?"+p.toString()+location.hash)}}})();</script>`;

/**
 * Theme, panel state, scroll position and auto-refresh. Every storage access is in try/catch:
 * with storage blocked the theme follows the OS, panels use their defaults and auto-refresh is on.
 * Without JavaScript the controls stay hidden and the page simply does not refresh.
 */
const PAGE_SCRIPT = `<script>(function(){
var K="${STORAGE_PREFIX}",N=${AUTO_REFRESH_SECONDS},D=document,R=D.documentElement;
function get(k){try{return localStorage.getItem(K+k)}catch(e){return null}}
function set(k,v){try{localStorage.setItem(K+k,v)}catch(e){}}
D.getElementById("ctl").hidden=false;
var ut=D.getElementById("ui-toggle");if(ut)ut.onclick=function(){set("ui","new")};
var th=D.getElementById("theme");th.value=R.getAttribute("data-theme")||"system";
th.onchange=function(){if(th.value==="system")R.removeAttribute("data-theme");else R.setAttribute("data-theme",th.value);set("theme",th.value)};
var ps={};try{ps=JSON.parse(get("panels")||"{}")||{}}catch(e){ps={}}
var all=D.querySelectorAll("details[id]");
for(var i=0;i<all.length;i++){if(typeof ps[all[i].id]==="boolean")all[i].open=ps[all[i].id]}
D.addEventListener("toggle",function(e){var t=e.target;if(t&&t.tagName==="DETAILS"&&t.id){ps[t.id]=t.open;set("panels",JSON.stringify(ps))}},true);
function setAll(o){for(var i=0;i<all.length;i++)all[i].open=o}
D.getElementById("expand").onclick=function(){setAll(true)};
D.getElementById("collapse").onclick=function(){setAll(false)};
try{if("scrollRestoration" in history)history.scrollRestoration="manual";var sv=JSON.parse(sessionStorage.getItem(K+"scroll")||"null");if(sv&&sv.href===location.href)window.scrollTo(0,sv.y)}catch(e){}
addEventListener("pagehide",function(){try{sessionStorage.setItem(K+"scroll",JSON.stringify({href:location.href,y:window.scrollY}))}catch(e){}});
var ar=D.getElementById("ar"),arc=D.getElementById("arc"),due=Date.now()+N*1000,timer=null;
ar.checked=get("autorefresh")!=="off";
function tick(){if(!ar.checked){arc.textContent="";return}var s=Math.ceil((due-Date.now())/1000);if(s<=0){stop();location.reload();return}arc.textContent="next refresh in "+s+" s"}
function stop(){if(timer){clearInterval(timer);timer=null}}
function start(){stop();tick();if(ar.checked&&!D.hidden&&timer===null)timer=setInterval(tick,1000)}
ar.onchange=function(){set("autorefresh",ar.checked?"on":"off");due=Date.now()+N*1000;start()};
D.addEventListener("visibilitychange",function(){if(D.hidden)stop();else start()});
start();
})();</script>`;

/** Paid spend panel: per day as bars (with the daily cap), per agent as a table. Estimates from token usage. */
function costsLegacy(d: DashboardDoc): string {
  const c = d.costs;
  const max = Math.max(c.today.cap_usd, ...c.by_day.map((x) => x.usd), 0.0001);
  const bars = c.by_day
    .map((x) => `<div class="bar1" title="${x.day}: ${money(x.usd)}"><div style="height:${Math.round((x.usd / max) * 100)}%"></div><span>${x.day.slice(5)}</span><b>${money(x.usd)}</b></div>`)
    .join("");
  const rows = c.by_agent.map((a) => [esc(a.agent), money(a.usd), String(a.paid_runs), String(a.runs)]);
  return `<p class="dim">Estimated from token usage and billing.prices_usd_per_m; runs on the free key cost nothing. Daily cap ${money(c.today.cap_usd)}, per run ${money(c.today.run_cap_usd)}, resets ${clock(c.today.resets_at)} UTC.</p>
<div class="bars">${bars}</div><div class="wrap">${table(["agent", "paid", "paid runs", "runs"], rows, [1, 2, 3])}</div>`;
}

export function dashboardHtmlLegacy(d: DashboardDoc): string {
  const now = new Date(d.generated_at);
  const repo = d.target_repo;
  const human = d.blocked.filter((b) => b.needs_human);
  const failed = d.happened.runs.filter((r) => ["failed", "timeout", "lost"].includes(r.status)).length;
  const used = d.today.model_calls + d.today.reserved;
  const pct = Math.min(100, Math.round((used / Math.max(1, d.today.quota)) * 100));
  const maxCalls = Math.max(1, ...d.happened.calls_by_day.map((x) => x.calls));
  const windowLinks = [24, 48, 168].map((h) => (h === d.window_hours ? `<b>${h} h</b>` : `<a href="${dashboardHref(h, "old")}">${h} h</a>`)).join(" · ");

  const tiles = [
    ["Factory", d.paused ? `<span class="pill bad">&#10007; paused</span>` : `<span class="pill good">&#10003; running</span>`, `next tick ${ago(d.next.next_tick_at, now)}`],
    ["Needs you", String(human.length), human.length ? `${d.blocked.length} blocked in all` : "nothing waiting on a human"],
    ["In flight", String(d.in_flight.length), d.in_flight.some((f) => f.overdue) ? "one or more overdue" : d.in_flight.map((f) => f.agent).join(", ") || "idle"],
    [`Runs, last ${d.window_hours} h`, String(d.happened.runs.length), failed ? `${failed} failed` : "no failures"],
    ["Model calls today", `${used} <span class="dim">/ ${d.today.quota}</span>`, `<div class="meter"><div style="width:${pct}%"></div></div>${d.today.model_calls} used, ${d.today.reserved} reserved, ${d.today.remaining} left`],
    ["Paid spend today", `${money(d.costs.today.spent_usd + d.costs.today.reserved_usd)} <span class="dim">/ ${money(d.costs.today.cap_usd)}</span>`, d.costs.today.cap_usd > 0
      ? `<div class="meter"><div style="width:${Math.min(100, Math.round(((d.costs.today.spent_usd + d.costs.today.reserved_usd) / d.costs.today.cap_usd) * 100))}%"></div></div>${money(d.costs.today.spent_usd)} spent, ${money(d.costs.today.reserved_usd)} reserved${d.costs.today.blocked ? `, <span class="pill bad">paid key blocked</span>` : ""}`
      : "no paid key"],
    ["Shipped", String(d.happened.target.merged_prs.length + d.happened.target.closed_issues.length), `${d.happened.target.merged_prs.length} PRs merged, ${d.happened.target.closed_issues.length} issues closed`],
  ]
    .map(([l, v, s]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`)
    .join("");

  // One card per kind; the items inside share a cause, so the reason reads once per group when it repeats.
  const groups: { kind: string; items: Blocker[] }[] = [];
  const ordered = [...human, ...d.blocked.filter((b) => !b.needs_human)];
  for (const b of ordered) {
    const g = groups[groups.length - 1];
    if (g && g.kind === b.kind) g.items.push(b);
    else groups.push({ kind: b.kind, items: [b] });
  }
  const blockedHtml = groups.length
    ? groups
        .map(({ kind, items }) => {
          const you = items[0].needs_human;
          const shared = items.length > 1 && items.every((b) => b.reason === items[0].reason);
          const rows = items
            .map(
              (b) =>
                `<div class="item"><div>${link(b.url, b.subject)} <span class="dim">${trunc(b.title)}</span></div><div class="todo">${b.needs_human ? "<b>To do:</b> " : "<b>Waiting:</b> "}${esc(b.todo)}</div>${shared ? "" : `<div class="dim">${esc(b.reason)}</div>`}<code class="act">${esc(b.action)}</code></div>`,
            )
            .join("");
          return `<div class="blk"><h3><span class="pill ${you ? "bad" : "mute"}">${you ? "&#9679; you" : "&#9675; waits"}</span> ${esc(kind)} <span class="dim">(${items.length})</span></h3>${shared ? `<p class="dim">${esc(items[0].reason)}</p>` : ""}${rows}</div>`;
        })
        .join("")
    : `<p class="empty">nothing is blocked</p>`;

  const flightRows = d.in_flight.map((f) => [
    esc(f.agent),
    subjectLink(repo, f.subject, null, null),
    pill(f.status) + (f.overdue ? ' <span class="pill bad">&#9888; overdue</span>' : ""),
    when(f.dispatched_at, now),
    link(f.workflow_run_url, f.workflow_run_url ? "run" : ""),
  ]);

  const queueHtml = d.next.queue
    .map(({ agent, gate, items }) => {
      const gateText = gate ? `<span class="pill ${gate === "in_flight" ? "warn" : "mute"}">${esc(gate)}</span>` : "";
      const first = items.find((i) => i.eligible);
      const lead = !items.length
        ? `<p class="empty">nothing in its backlog</p>`
        : gate === "in_flight"
          ? `<p>busy now; ${first ? `then #${first.number}` : "nothing eligible after that"}</p>`
          : gate
            ? `<p>held (${esc(gate)}); first eligible would be ${first ? `#${first.number}` : "none"}</p>`
            : first
              ? `<p>next tick dispatches <b>${link(first.url, `#${first.number}`)}</b> ${trunc(first.title)}</p>`
              : `<p>every candidate is blocked (see above)</p>`;
      const list = items.length
        ? `<ol>${items.map((i) => `<li class="${i.eligible ? "ok" : "held"}">${link(i.url, `#${i.number}`)} ${trunc(i.title)} ${i.eligible ? "" : `<span class="pill mute">${esc(i.reason ?? "")}</span>${queueHint(i.reason) ? ` <span class="hint">${esc(queueHint(i.reason))}</span>` : ""}`}</li>`).join("")}</ol>`
        : "";
      return `<div class="q"><h3>${esc(agent)} ${gateText}</h3>${lead}${list}</div>`;
    })
    .join("");

  const schedRows = d.next.scheduled.map((s) => [esc(s.agent), s.at ? when(s.at, now) : "event-driven", esc(s.note)]);
  const decisions = (d.next.last_tick?.decisions ?? []).map((x) => `<li><code>${esc(JSON.stringify(x))}</code></li>`).join("");

  const t = d.happened.target;
  const movement = [
    ...t.merged_prs.map((p) => ({ at: p.merged_at, cells: ['<span class="pill good">&#10003; merged</span>', link(p.url, `PR #${p.number}`), trunc(p.title), esc(p.author)] })),
    ...t.closed_issues.map((i) => ({ at: i.closed_at, cells: ['<span class="pill good">&#10003; closed</span>', link(i.url, `#${i.number}`), trunc(i.title), esc(i.labels.filter((l) => l.startsWith("status:")).join(" "))] })),
    ...t.closed_prs.map((p) => ({ at: p.closed_at, cells: ['<span class="pill mute">&#8211; closed unmerged</span>', link(p.url, `PR #${p.number}`), trunc(p.title), esc(p.author)] })),
  ]
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((m) => [when(m.at, now), ...m.cells]);
  const queued = d.next.queue.reduce((n, q) => n + q.items.length, 0);

  const runRows = d.happened.runs.map((r) => [
    when(r.dispatched_at, now),
    esc(r.agent),
    subjectLink(repo, r.subject, r.issue_number, r.pr_number),
    esc(r.trigger ?? ""),
    pill(r.status) + (r.stop_reason && r.stop_reason !== "end_turn" ? ` <span class="dim">${esc(r.stop_reason)}</span>` : ""),
    r.duration_min === null ? "" : `${r.duration_min} min`,
    String(r.model_calls),
    r.paid_cost_usd > 0 ? money(r.paid_cost_usd) : "",
    link(r.workflow_run_url, r.workflow_run_url ? "log" : ""),
  ]);
  const agents = Object.keys(d.happened.counts).sort();
  const statuses = ["success", "failed", "timeout", "lost", "denied", "in_flight"];
  const countRows = agents.map((a) => [esc(a), ...statuses.map((s) => String(d.happened.counts[a][s] ?? ""))]);
  const bars = d.happened.calls_by_day
    .map((x) => `<div class="bar1" title="${x.day}: ${x.calls} calls"><div style="height:${Math.round((x.calls / maxCalls) * 100)}%"></div><span>${x.day.slice(5)}</span><b>${x.calls}</b></div>`)
    .join("");

  const nav = [["p-blocked", "Blocked"], ["p-flight", "In flight"], ["p-next", "Next"], ["p-happened", "Happened"]]
    .map(([id, text]) => `<a href="#${id}">${text}</a>`)
    .join("");
  const controls = `<div class="ctl" id="ctl" hidden><label>theme <select id="theme" aria-label="Colour theme"><option value="system">system</option><option value="light">light</option><option value="dark">dark</option></select></label><label><input type="checkbox" id="ar"> auto-refresh (${AUTO_REFRESH_SECONDS} s)</label><span id="arc" class="dim"></span><button type="button" id="expand">expand all</button><button type="button" id="collapse">collapse all</button></div>`;

  const blockedBody = `<p class="dim">&#9679; you: a human has to act (listed first). &#9675; waits: clears on its own (a dependency closes, a run finishes, the day rolls over).</p><div class="blocks">${blockedHtml}</div>`;
  const nextBody = `<p class="dim">Next tick ${clock(d.next.next_tick_at)} UTC (${ago(d.next.next_tick_at, now)}). Candidate order is the tick's own: the first eligible item per agent is dispatched if quota and locks allow.</p>
<div class="queues">${queueHtml}</div>
${panel("p-scheduled", 3, "Scheduled", d.next.scheduled.length, true, `<div class="wrap">${table(["agent", "when (UTC)", "what"], schedRows)}</div>`)}
<details id="p-decisions"><summary>Last tick ${d.next.last_tick ? when(d.next.last_tick.at, now) : "never"}: ${(d.next.last_tick?.decisions ?? []).length} decisions</summary><ul>${decisions || "<li>none</li>"}</ul></details>`;
  const happenedBody = [
    panel("p-movement", 3, "Target movement", movement.length, false, `<div class="wrap">${table(["when (UTC)", "", "what", "title", ""], movement)}</div>`),
    panel("p-calls", 3, "Model calls per day", d.happened.calls_by_day.length, false, `<div class="bars">${bars}</div>`),
    panel("p-costs", 3, `Paid spend: ${money(d.costs.window_usd)} in ${d.window_hours} h`, d.costs.by_agent.filter((a) => a.usd > 0).length, false, costsLegacy(d)),
    panel("p-counts", 3, "Runs by agent", agents.length, false, `<div class="wrap">${table(["agent", ...statuses], countRows, statuses.map((_, i) => i + 1))}</div>`),
    panel("p-runs", 3, "Runs", d.happened.runs.length, false, `<div class="wrap">${table(["dispatched (UTC)", "agent", "subject", "trigger", "outcome", "took", "calls", "paid", ""], runRows, [5, 6, 7])}</div>`),
  ].join("\n");

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Factory dashboard</title>
${THEME_BOOT}
<style>
:root{color-scheme:light;${tokens(THEMES.light)}}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;${tokens(THEMES.dark)}}}
:root[data-theme="dark"]{color-scheme:dark;${tokens(THEMES.dark)}}
*{box-sizing:border-box}body{margin:0;padding:0 16px 16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--accent)}h1{font-size:20px;line-height:1.3;margin:0}h2{font-size:16px;margin:0}h3{font-size:14px;margin:0}
.bar{position:sticky;top:0;z-index:3;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0 8px;display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center}
@media (max-width:700px){.bar{position:static}}
.bar nav{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:13px}
.ui-sw{font-size:13px;padding:2px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);text-decoration:none}
.ctl{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;font-size:13px;color:var(--fg2);margin-left:auto}.ctl[hidden]{display:none}
.ctl select,.ctl button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:1px 8px;cursor:pointer}
.top{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;color:var(--fg2);font-size:13px;margin:8px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:12px 0}
.tile{background:var(--card);border-radius:8px;padding:10px 12px}.tile .label{color:var(--fg2);font-size:12px}.tile .value{font-size:22px;font-weight:600;margin:2px 0;font-variant-numeric:tabular-nums}.tile .sub{color:var(--fg2);font-size:12px}
.meter{height:6px;border-radius:3px;background:var(--line);margin:4px 0}.meter div{height:6px;border-radius:3px;background:var(--accent)}
details.panel{margin:0}details.p2{border-top:1px solid var(--line);padding:12px 0 4px;scroll-margin-top:56px}details.p3{margin:12px 0 4px}
details.panel>summary{cursor:pointer;list-style-position:outside;margin-left:16px}details.panel>summary h2,details.panel>summary h3{display:inline}
.count{color:var(--fg2);font-weight:400}.pbody{padding:8px 0 4px}
.wrap{overflow:auto;max-height:70vh;border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;vertical-align:top;padding:4px 8px;border-bottom:1px solid var(--line)}
th{color:var(--fg2);font-weight:500;font-size:12px;position:sticky;top:0;z-index:1;background:var(--card)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.trunc{display:inline-block;max-width:min(40ch,100%);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.pill{display:inline-block;padding:0 7px;border-radius:9px;font-size:12px;white-space:nowrap;border:1px solid}.good{color:var(--good);border-color:var(--good)}.bad{color:var(--bad);border-color:var(--bad)}.warn{color:var(--warn);border-color:var(--warn)}.mute{color:var(--mute);border-color:var(--mute)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}code.act{white-space:pre-wrap;word-break:break-all;display:block;color:var(--fg2)}
.dim{color:var(--fg2)}.todo{margin:2px 0}.hint{font-size:12px;color:var(--fg2);font-style:italic}.empty{color:var(--fg2);font-style:italic;margin:4px 0}p{margin:0 0 8px}
.blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:12px;align-items:start}.blk{background:var(--card);border-radius:8px;padding:8px 12px;min-width:0}.blk h3{margin:4px 0 6px}.blk p{margin:0 0 6px}.item{padding:6px 0;border-top:1px solid var(--line)}
.queues{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:12px;margin-bottom:4px}.q{background:var(--card);border-radius:8px;padding:8px 12px;min-width:0}.q h3{margin:4px 0}.q ol{margin:4px 0 0;padding-left:20px}.q li{margin:2px 0}.q li.held{color:var(--fg2)}
.bars{display:flex;gap:6px;align-items:flex-end;height:110px;margin:24px 0 28px}.bar1{flex:1;max-width:60px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;position:relative}.bar1 div{width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:1px}.bar1 span{font-size:11px;color:var(--fg2);position:absolute;bottom:-18px}.bar1 b{font-size:11px;font-weight:500;position:absolute;top:-16px}
#p-decisions{margin:12px 0}#p-decisions>summary{cursor:pointer;color:var(--fg2)}
footer{margin-top:16px;font-size:12px}
</style></head><body>
<div class="bar"><h1>Factory dashboard${d.paused ? ' <span class="pill bad">&#10007; PAUSED</span>' : ""}</h1><nav aria-label="Sections">${nav}</nav><a class="ui-sw" id="ui-toggle" href="${dashboardHref(d.window_hours, "new")}">New UI</a>${controls}</div>
<div class="top"><span>target ${link(`https://github.com/${repo}`, repo)}</span><span>generated ${esc(d.generated_at.slice(0, 19).replace("T", " "))} UTC</span><span>window ${windowLinks}</span><span><a href="?format=json&hours=${d.window_hours}">json</a> · <a href="/status">status</a></span></div>
${d.github_error ? `<p class="pill bad">&#9888; GitHub unreachable: ${esc(d.github_error)}. Issue, PR and queue sections are empty; the run ledger is still live.</p>` : ""}
<div class="tiles">${tiles}</div>

${panel("p-blocked", 2, "Blocked: what needs a human, and what is just waiting", d.blocked.length, true, blockedBody)}
${panel("p-flight", 2, "In flight", d.in_flight.length, true, `<div class="wrap">${table(["agent", "subject", "status", "dispatched (UTC)", ""], flightRows)}</div>`)}
${panel("p-next", 2, "Next: what the tick will do", queued, true, nextBody)}
${panel("p-happened", 2, `Happened: last ${d.window_hours} h`, d.happened.runs.length, true, happenedBody)}

<footer class="dim">Read-only. Times are UTC. With JavaScript on, the page reloads every ${AUTO_REFRESH_SECONDS} s while auto-refresh is ticked (the default; your choice, the theme and which panels are open are kept in this browser); without it, reload by hand.</footer>
${PAGE_SCRIPT}
</body></html>`;
}
