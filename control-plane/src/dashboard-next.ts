import {
  AUTO_REFRESH_SECONDS,
  STORAGE_PREFIX,
  ago,
  clock,
  dashboardHref,
  esc,
  link,
  subjectLink,
  tokens,
  trunc,
  when,
} from "./dashboard-shared";
import { queueHint, type Blocker, type DashboardDoc, type RunSummary } from "./dashboard";

/**
 * WCAG AA (>= 4.5:1) verified Light & Dark colour tokens for the GitHub Primer + Vercel Geist hybrid UI.
 */
export const NEXT_THEMES = {
  light: {
    bg: "#ffffff",
    card: "#f6f8fa",
    card2: "#eef2f6",
    elev: "#ffffff",
    line: "#d1d9e0",
    line2: "#e1e7ed",
    fg: "#1f2328",
    fg2: "#59636e",
    accent: "#0969da",
    accentBg: "#ddf4ff",
    good: "#116329",
    goodBg: "#dafbe1",
    goodLine: "#82e596",
    warn: "#7d4e00",
    warnBg: "#fff8c5",
    warnLine: "#e8c44a",
    bad: "#a40e26",
    badBg: "#ffebe9",
    badLine: "#ffaba8",
    mute: "#59636e",
    muteBg: "#eff1f3",
    muteLine: "#d1d9e0",
  },
  dark: {
    bg: "#0d1117",
    card: "#151b23",
    card2: "#1c232d",
    elev: "#161b22",
    line: "#30363d",
    line2: "#21262d",
    fg: "#f0f6fc",
    fg2: "#9198a1",
    accent: "#4493f8",
    accentBg: "#12243b",
    good: "#3fb950",
    goodBg: "#12261e",
    goodLine: "#238636",
    warn: "#d29922",
    warnBg: "#272115",
    warnLine: "#9e6a03",
    bad: "#f85149",
    badBg: "#2c1619",
    badLine: "#da3633",
    mute: "#9198a1",
    muteBg: "#21262d",
    muteLine: "#3d444d",
  },
} as const;

const SVG_CHECK = `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6.5"/></svg>`;
const SVG_X = `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>`;
const SVG_CLOCK = `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2.2 1.3"/></svg>`;
const SVG_DASH = `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 8h8"/></svg>`;
const SVG_ALERT = `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.2L14 13H2L8 2.2z"/><path d="M8 6.5v3"/><circle cx="8" cy="11.3" r=".6" fill="currentColor" stroke="none"/></svg>`;
const SVG_DOT = `<svg class="ico" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="4"/></svg>`;

/** Runs before first paint so theme, density and persisted UI version apply without flash. */
const BOOT_SCRIPT = `<script>(function(){
var K="${STORAGE_PREFIX}",R=document.documentElement;
try{var t=localStorage.getItem(K+"theme");if(t==="light"||t==="dark")R.setAttribute("data-theme",t)}catch(e){}
try{var d=localStorage.getItem(K+"density");if(d==="compact")R.setAttribute("data-density",d)}catch(e){}
var p=new URLSearchParams(location.search),u=p.get("ui");
if(u==="new"){try{localStorage.setItem(K+"ui","new")}catch(e){}}
else if(!u){var s=null;try{s=localStorage.getItem(K+"ui")}catch(e){}if(s==="old"){p.set("ui","old");location.replace("?"+p.toString()+location.hash)}}
})();</script>`;

/** Enhances controls, density, panel persistence, scroll restoration, filters, copy buttons, and auto-refresh. */
const PAGE_SCRIPT = `<script>(function(){
var K="${STORAGE_PREFIX}",N=${AUTO_REFRESH_SECONDS},D=document,R=D.documentElement;
function get(k){try{return localStorage.getItem(K+k)}catch(e){return null}}
function set(k,v){try{localStorage.setItem(K+k,v)}catch(e){}}
D.getElementById("ctl").hidden=false;
var fb=D.querySelector(".filter-bar");if(fb)fb.hidden=false;
var rf=D.querySelector(".run-filters");if(rf)rf.hidden=false;
var cbs=D.querySelectorAll(".copy-btn");for(var j=0;j<cbs.length;j++)cbs[j].hidden=false;
var th=D.getElementById("theme");th.value=R.getAttribute("data-theme")||"system";
th.onchange=function(){if(th.value==="system")R.removeAttribute("data-theme");else R.setAttribute("data-theme",th.value);set("theme",th.value)};
var dn=D.getElementById("density");dn.value=R.getAttribute("data-density")==="compact"?"compact":"comfortable";
dn.onchange=function(){if(dn.value==="compact")R.setAttribute("data-density","compact");else R.removeAttribute("data-density");set("density",dn.value)};
var ut=D.getElementById("ui-toggle");if(ut)ut.onclick=function(){set("ui","old")};
var ps={};try{ps=JSON.parse(get("panels")||"{}")||{}}catch(e){ps={}}
var all=D.querySelectorAll("details[id]");
for(var i=0;i<all.length;i++){if(typeof ps[all[i].id]==="boolean")all[i].open=ps[all[i].id]}
D.addEventListener("toggle",function(e){var t=e.target;if(t&&t.tagName==="DETAILS"&&t.id){ps[t.id]=t.open;set("panels",JSON.stringify(ps))}},true);
function setAll(o){for(var i=0;i<all.length;i++)all[i].open=o}
D.getElementById("expand").onclick=function(){setAll(true)};
D.getElementById("collapse").onclick=function(){setAll(false)};
try{if("scrollRestoration" in history)history.scrollRestoration="manual";var sv=JSON.parse(sessionStorage.getItem(K+"scroll")||"null");if(sv&&sv.href===location.href)window.scrollTo(0,sv.y)}catch(e){}
addEventListener("pagehide",function(){try{sessionStorage.setItem(K+"scroll",JSON.stringify({href:location.href,y:window.scrollY}))}catch(e){}});
if(fb){fb.addEventListener("click",function(e){
  var b=e.target&&e.target.closest?e.target.closest("[data-filter]"):null;if(!b)return;
  var f=b.getAttribute("data-filter"),btns=fb.querySelectorAll("[data-filter]"),cards=D.querySelectorAll(".blk"),vis=0;
  for(var k=0;k<btns.length;k++){btns[k].classList.toggle("active",btns[k]===b);btns[k].setAttribute("aria-pressed",btns[k]===b?"true":"false")}
  for(var k=0;k<cards.length;k++){var show=f==="all"||cards[k].getAttribute("data-kind")===f;cards[k].hidden=!show;if(show)vis++}
  var em=D.getElementById("blk-filter-empty");if(em)em.hidden=vis>0||cards.length===0;
})}
if(rf){rf.addEventListener("click",function(e){
  var b=e.target&&e.target.closest?e.target.closest("[data-run-filter]"):null;if(!b)return;
  var f=b.getAttribute("data-run-filter"),btns=rf.querySelectorAll("[data-run-filter]"),rows=D.querySelectorAll("#p-runs tbody tr");
  for(var k=0;k<btns.length;k++){btns[k].classList.toggle("active",btns[k]===b);btns[k].setAttribute("aria-pressed",btns[k]===b?"true":"false")}
  for(var k=0;k<rows.length;k++){
    var r=rows[k],show=f==="all"||(f==="failed"?r.getAttribute("data-failed")==="1":r.getAttribute("data-agent")===f.slice(6));
    r.hidden=!show;
  }
})}
D.addEventListener("click",function(e){
  var b=e.target&&e.target.closest?e.target.closest(".copy-btn"):null;if(!b)return;
  var txt=b.getAttribute("data-copy")||"";
  function ok(){b.textContent="Copied";b.classList.add("copied");setTimeout(function(){b.textContent="Copy";b.classList.remove("copied")},1500)}
  function fallback(){try{var ta=D.createElement("textarea");ta.value=txt;ta.style.position="fixed";ta.style.opacity="0";D.body.appendChild(ta);ta.select();D.execCommand("copy");D.body.removeChild(ta);ok()}catch(err){}}
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(ok,fallback)}else{fallback()}
});
var ar=D.getElementById("ar"),arc=D.getElementById("arc"),due=Date.now()+N*1000,timer=null;
ar.checked=get("autorefresh")!=="off";
function tick(){if(!ar.checked){arc.textContent="";return}var s=Math.ceil((due-Date.now())/1000);if(s<=0){stop();location.reload();return}arc.textContent="next refresh in "+s+" s"}
function stop(){if(timer){clearInterval(timer);timer=null}}
function start(){stop();tick();if(ar.checked&&!D.hidden&&timer===null)timer=setInterval(tick,1000)}
ar.onchange=function(){set("autorefresh",ar.checked?"on":"off");due=Date.now()+N*1000;start()};
D.addEventListener("visibilitychange",function(){if(D.hidden)stop();else start()});
start();
})();</script>`;

function panel(id: string, level: 2 | 3, title: string, count: number | null, open: boolean, body: string): string {
  const n = count === null ? "" : ` <span class="count">(${count})</span>`;
  return `<details class="panel p${level}" id="${id}"${open ? " open" : ""}><summary><h${level}>${title}${n}</h${level}></summary><div class="pbody">${body}</div></details>`;
}

function statusPill(status: string): string {
  const cls = ["success"].includes(status)
    ? "good"
    : ["failed", "timeout", "lost"].includes(status)
      ? "bad"
      : ["dispatched", "started", "in_flight"].includes(status)
        ? "warn"
        : "mute";
  const icon = cls === "good" ? SVG_CHECK : cls === "bad" ? SVG_X : cls === "warn" ? SVG_CLOCK : SVG_DASH;
  return `<span class="pill ${cls}">${icon} ${esc(status)}</span>`;
}

function renderTable(head: string[], rows: string[][], num: number[] = [], rowAttrs?: string[]): string {
  if (!rows.length) return `<p class="empty">none</p>`;
  const cls = (i: number) => (num.includes(i) ? ' class="num"' : "");
  const thead = `<thead><tr>${head.map((h, i) => `<th${cls(i)}>${h}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${rows
    .map((r, ri) => `<tr${rowAttrs?.[ri] ? ` ${rowAttrs[ri]}` : ""}>${r.map((c, i) => `<td${cls(i)}>${c}</td>`).join("")}</tr>`)
    .join("")}</tbody>`;
  return `<table>${thead}${tbody}</table>`;
}

function renderCallsSvg(days: { day: string; calls: number }[], quota: number): string {
  if (!days.length) return `<p class="empty">none</p>`;
  const maxCalls = Math.max(1, ...days.map((x) => x.calls));
  const showQuota = quota > 0 && quota <= maxCalls * 1.6;
  const maxY = showQuota ? Math.max(maxCalls, quota) : maxCalls;

  const padL = 38;
  const padR = 14;
  const padT = 22;
  const padB = 26;
  const plotH = 110;
  const step = Math.max(46, Math.min(76, Math.floor(620 / Math.max(1, days.length))));
  const plotW = Math.max(220, days.length * step);
  const w = padL + plotW + padR;
  const h = padT + plotH + padB;
  const barW = Math.max(14, Math.min(38, Math.round(step * 0.56)));

  const gridLines = [0, 0.5, 1]
    .map((f) => {
      const y = (padT + plotH * (1 - f)).toFixed(1);
      const val = Math.round(maxY * f);
      return `<line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="var(--line2)" stroke-width="1"/><text x="${padL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" fill="var(--fg2)" font-size="10">${val}</text>`;
    })
    .join("");

  const quotaLine = showQuota
    ? (() => {
        const qy = (padT + plotH * (1 - quota / maxY)).toFixed(1);
        return `<line x1="${padL}" y1="${qy}" x2="${padL + plotW}" y2="${qy}" stroke="var(--warn)" stroke-width="1.25" stroke-dasharray="4 3"/><text x="${padL + plotW}" y="${Number(qy) - 4}" text-anchor="end" fill="var(--warn)" font-size="10" font-weight="600">quota ${quota}</text>`;
      })()
    : "";

  const bars = days
    .map((x, idx) => {
      const cx = padL + idx * step + step / 2;
      const bx = (cx - barW / 2).toFixed(1);
      const bh = x.calls > 0 ? Math.max(2, (x.calls / maxY) * plotH) : 1;
      const by = (padT + plotH - bh).toFixed(1);
      return `<g class="svg-bar"><title>${esc(x.day)}: ${x.calls} calls</title><rect x="${bx}" y="${by}" width="${barW}" height="${bh.toFixed(1)}" rx="3" fill="${x.calls > 0 ? "var(--accent)" : "var(--line)"}"/><text x="${cx.toFixed(1)}" y="${(Number(by) - 5).toFixed(1)}" text-anchor="middle" fill="var(--fg)" font-size="10.5" font-weight="600">${x.calls}</text><text x="${cx.toFixed(1)}" y="${padT + plotH + 15}" text-anchor="middle" fill="var(--fg2)" font-size="10.5">${esc(x.day.slice(5))}</text></g>`;
    })
    .join("");

  return `<div class="chart-wrap"><svg class="calls-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="Model calls per UTC day">${gridLines}${quotaLine}${bars}</svg></div>`;
}

function isFailedRun(r: RunSummary): boolean {
  return ["failed", "timeout", "lost"].includes(r.status);
}

/** Renders the New UI (GitHub Primer + Vercel Geist Hybrid) HTML dashboard. */
export function dashboardHtmlNext(d: DashboardDoc): string {
  const now = new Date(d.generated_at);
  const repo = d.target_repo;
  const human = d.blocked.filter((b) => b.needs_human);
  const waits = d.blocked.filter((b) => !b.needs_human);
  const failed = d.happened.runs.filter(isFailedRun).length;
  const quota = Math.max(1, d.today.quota);
  const used = d.today.model_calls + d.today.reserved;
  const totalPct = Math.round((used / quota) * 100);
  const usedPct = Math.min(100, (d.today.model_calls / quota) * 100);
  const resPct = Math.min(100 - usedPct, (d.today.reserved / quota) * 100);
  const meterTone = totalPct >= 90 ? "var(--bad)" : totalPct >= 75 ? "var(--warn)" : "var(--accent)";

  const tiles = [
    {
      cls: "",
      label: "Factory",
      value: d.paused ? `<span class="pill bad">${SVG_X} paused</span>` : `<span class="pill good">${SVG_CHECK} running</span>`,
      sub: `next tick ${esc(ago(d.next.next_tick_at, now))}`,
    },
    {
      cls: human.length > 0 ? " tile-alert" : "",
      label: "Needs you",
      value: String(human.length),
      sub: human.length ? `${d.blocked.length} blocked in all` : "nothing waiting on a human",
    },
    {
      cls: "",
      label: d.in_flight.length > 0 ? `In flight <span class="pulse-dot" aria-hidden="true"></span>` : "In flight",
      value: String(d.in_flight.length),
      sub: d.in_flight.some((f) => f.overdue)
        ? `<span class="bad-txt">one or more overdue</span>`
        : esc(d.in_flight.map((f) => f.agent).join(", ") || "idle"),
    },
    {
      cls: "",
      label: `Runs, last ${d.window_hours} h`,
      value: String(d.happened.runs.length),
      sub: failed ? `<span class="bad-txt">${failed} failed</span>` : "no failures",
    },
    {
      cls: "",
      label: "Model calls today",
      value: `${used} <span class="dim quota-den">/ ${d.today.quota}</span>`,
      sub: `<div class="meter" role="progressbar" aria-valuenow="${Math.min(100, totalPct)}" aria-valuemin="0" aria-valuemax="100" aria-label="Daily model call quota"><div class="m-used" style="width:${usedPct.toFixed(1)}%;background:${meterTone}"></div><div class="m-res" style="width:${resPct.toFixed(1)}%"></div></div>${d.today.model_calls} used, ${d.today.reserved} reserved, ${d.today.remaining} left`,
    },
    {
      cls: "",
      label: "Shipped",
      value: String(d.happened.target.merged_prs.length + d.happened.target.closed_issues.length),
      sub: `${d.happened.target.merged_prs.length} PRs merged, ${d.happened.target.closed_issues.length} issues closed`,
    },
  ]
    .map(
      (t) =>
        `<div class="tile${t.cls}"><div class="label">${t.label}</div><div class="value">${t.value}</div><div class="sub">${t.sub}</div></div>`,
    )
    .join("");

  const groups: { kind: string; items: Blocker[] }[] = [];
  for (const b of [...human, ...waits]) {
    const g = groups[groups.length - 1];
    if (g && g.kind === b.kind) g.items.push(b);
    else groups.push({ kind: b.kind, items: [b] });
  }
  const filterBar = `<div class="filter-bar" hidden role="group" aria-label="Filter blocked items"><button type="button" class="chip active" data-filter="all" aria-pressed="true">All (${d.blocked.length})</button><button type="button" class="chip" data-filter="human" aria-pressed="false">Needs you (${human.length})</button><button type="button" class="chip" data-filter="waits" aria-pressed="false">Waiting (${waits.length})</button></div>`;
  const blockedCards = groups.length
    ? groups
        .map(({ kind, items }) => {
          const you = items[0].needs_human;
          const shared = items.length > 1 && items.every((b) => b.reason === items[0].reason);
          const rows = items
            .map(
              (b) =>
                `<div class="item"><div class="item-head"><span class="mono">${link(b.url, b.subject)}</span> ${trunc(b.title)}</div><div class="todo ${b.needs_human ? "todo-you" : "todo-wait"}"><b>${b.needs_human ? "To do:" : "Waiting:"}</b> ${esc(b.todo)}</div>${shared ? "" : `<div class="dim reason">${esc(b.reason)}</div>`}<div class="cmd-wrap"><code class="act">${esc(b.action)}</code><button type="button" class="copy-btn" data-copy="${esc(b.action)}" hidden>Copy</button></div></div>`,
            )
            .join("");
          return `<div class="blk ${you ? "blk-you" : "blk-wait"}" data-kind="${you ? "human" : "waits"}"><h3><span class="pill ${you ? "bad" : "mute"}">${you ? SVG_DOT : SVG_CLOCK} ${you ? "you" : "waits"}</span> <span class="blk-kind">${esc(kind)}</span> <span class="count-badge">${items.length}</span></h3>${shared ? `<p class="dim blk-shared">${esc(items[0].reason)}</p>` : ""}${rows}</div>`;
        })
        .join("")
    : `<p class="empty">nothing is blocked</p>`;
  const blockedBody = `<p class="dim">${SVG_DOT} <b>you</b>: a human has to act (listed first). ${SVG_CLOCK} <b>waits</b>: clears on its own (a dependency closes, a run finishes, the day rolls over).</p>${filterBar}<div class="blocks">${blockedCards}</div><p class="empty" id="blk-filter-empty" hidden>No blocked items match this filter.</p>`;

  const flightRows = d.in_flight.map((f) => [
    `<span class="mono">${esc(f.agent)}</span>`,
    `<span class="mono">${subjectLink(repo, f.subject, null, null)}</span>`,
    statusPill(f.status) + (f.overdue ? ` <span class="pill bad">${SVG_ALERT} overdue</span>` : ""),
    when(f.dispatched_at, now),
    link(f.workflow_run_url, f.workflow_run_url ? "Run log \u2192" : ""),
  ]);

  const queueHtml = d.next.queue
    .map(({ agent, gate, items }) => {
      const gateText = gate ? `<span class="pill ${gate === "in_flight" ? "warn" : "mute"}">${esc(gate)}</span>` : "";
      const first = items.find((i) => i.eligible);
      const lead = !items.length
        ? `<p class="empty">nothing in its backlog</p>`
        : gate === "in_flight"
          ? `<p class="q-lead">busy now; ${first ? `then #${first.number}` : "nothing eligible after that"}</p>`
          : gate
            ? `<p class="q-lead">held (${esc(gate)}); first eligible would be ${first ? `#${first.number}` : "none"}</p>`
            : first
              ? `<p class="q-lead q-lead-go">next tick dispatches <b>${link(first.url, `#${first.number}`)}</b> ${trunc(first.title)}</p>`
              : `<p class="q-lead">every candidate is blocked (see above)</p>`;
      const list = items.length
        ? `<ol>${items
            .map((i) => {
              const badge = i.eligible
                ? i.reason === "revise"
                  ? ` <span class="pill accent-pill">revise</span>`
                  : ""
                : ` <span class="pill mute">${esc(i.reason ?? "")}</span>${queueHint(i.reason) ? ` <span class="hint">${esc(queueHint(i.reason))}</span>` : ""}`;
              return `<li class="${i.eligible ? "ok" : "held"}"><span class="mono">${link(i.url, `#${i.number}`)}</span> ${trunc(i.title)}${badge}</li>`;
            })
            .join("")}</ol>`
        : "";
      return `<div class="q"><div class="q-head"><h3>${esc(agent)}</h3>${gateText}</div>${lead}${list}</div>`;
    })
    .join("");

  const schedRows = d.next.scheduled.map((s) => [
    `<span class="mono">${esc(s.agent)}</span>`,
    s.at ? when(s.at, now) : "event-driven",
    esc(s.note),
  ]);
  const decisions = (d.next.last_tick?.decisions ?? []).map((x) => `<li><code>${esc(JSON.stringify(x))}</code></li>`).join("");

  const t = d.happened.target;
  const movement = [
    ...t.merged_prs.map((p) => ({
      at: p.merged_at,
      cells: [`<span class="pill good">${SVG_CHECK} merged</span>`, `<span class="mono">${link(p.url, `PR #${p.number}`)}</span>`, trunc(p.title), esc(p.author)],
    })),
    ...t.closed_issues.map((i) => ({
      at: i.closed_at,
      cells: [
        `<span class="pill good">${SVG_CHECK} closed</span>`,
        `<span class="mono">${link(i.url, `#${i.number}`)}</span>`,
        trunc(i.title),
        esc(i.labels.filter((l) => l.startsWith("status:")).join(" ")),
      ],
    })),
    ...t.closed_prs.map((p) => ({
      at: p.closed_at,
      cells: [`<span class="pill mute">${SVG_DASH} closed unmerged</span>`, `<span class="mono">${link(p.url, `PR #${p.number}`)}</span>`, trunc(p.title), esc(p.author)],
    })),
  ]
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((m) => [when(m.at, now), ...m.cells]);

  const queued = d.next.queue.reduce((n, q) => n + q.items.length, 0);
  const runRows = d.happened.runs.map((r) => [
    when(r.dispatched_at, now),
    `<span class="mono">${esc(r.agent)}</span>`,
    `<span class="mono">${subjectLink(repo, r.subject, r.issue_number, r.pr_number)}</span>`,
    esc(r.trigger ?? ""),
    statusPill(r.status) + (r.stop_reason && r.stop_reason !== "end_turn" ? ` <span class="dim">${esc(r.stop_reason)}</span>` : ""),
    r.duration_min === null ? "" : `${r.duration_min} min`,
    String(r.model_calls),
    link(r.workflow_run_url, r.workflow_run_url ? "log" : ""),
  ]);
  const runRowAttrs = d.happened.runs.map(
    (r) => `data-agent="${esc(r.agent)}" data-failed="${isFailedRun(r) ? "1" : "0"}"`,
  );
  const runAgents = [...new Set(d.happened.runs.map((r) => r.agent))].sort();
  const runFilters = `<div class="run-filters" hidden role="group" aria-label="Filter runs"><button type="button" class="chip active" data-run-filter="all" aria-pressed="true">All (${d.happened.runs.length})</button><button type="button" class="chip" data-run-filter="failed" aria-pressed="false">Failed only (${failed})</button>${runAgents
    .map((a) => `<button type="button" class="chip" data-run-filter="agent:${esc(a)}" aria-pressed="false">${esc(a)}</button>`)
    .join("")}</div>`;

  const agents = Object.keys(d.happened.counts).sort();
  const statuses = ["success", "failed", "timeout", "lost", "denied", "in_flight"];
  const countRows = agents.map((a) => [
    `<span class="mono">${esc(a)}</span>`,
    ...statuses.map((s) => {
      const v = d.happened.counts[a][s];
      return v ? String(v) : `<span class="zero">${v === 0 ? "0" : "\u2013"}</span>`;
    }),
  ]);

  const nav = [
    ["p-blocked", "Blocked"],
    ["p-flight", "In flight"],
    ["p-next", "Next"],
    ["p-happened", "Happened"],
  ]
    .map(([id, label]) => `<a href="#${id}">${label}</a>`)
    .join("");

  const windowLinks = [24, 48, 168]
    .map((h) => `<a class="${h === d.window_hours ? "win-on" : ""}" href="${dashboardHref(h, "new")}"${h === d.window_hours ? ' aria-current="page"' : ""}>${h} h</a>`)
    .join("");

  const controls = `<div class="ctl" id="ctl" hidden><label>Theme <select id="theme" aria-label="Colour theme"><option value="system">system</option><option value="light">light</option><option value="dark">dark</option></select></label><label>Density <select id="density" aria-label="Layout density"><option value="comfortable">comfortable</option><option value="compact">compact</option></select></label><label class="ar-lbl"><input type="checkbox" id="ar"> auto-refresh (${AUTO_REFRESH_SECONDS} s)</label><span id="arc" class="dim"></span><button type="button" id="expand">Expand all</button><button type="button" id="collapse">Collapse all</button></div>`;

  const nextBody = `<p class="dim">Next tick ${clock(d.next.next_tick_at)} UTC (${ago(d.next.next_tick_at, now)}). Candidate order is the tick's own: the first eligible item per agent is dispatched if quota and locks allow.</p>
<div class="queues">${queueHtml}</div>
${panel("p-scheduled", 3, "Scheduled", d.next.scheduled.length, true, `<div class="wrap">${renderTable(["agent", "when (UTC)", "what"], schedRows)}</div>`)}
<details id="p-decisions"><summary>Last tick ${d.next.last_tick ? when(d.next.last_tick.at, now) : "never"}: ${(d.next.last_tick?.decisions ?? []).length} decisions</summary><ul>${decisions || "<li>none</li>"}</ul></details>`;

  const happenedBody = [
    panel("p-movement", 3, "Target movement", movement.length, false, `<div class="wrap">${renderTable(["when (UTC)", "", "what", "title", ""], movement)}</div>`),
    panel("p-calls", 3, "Model calls per day", d.happened.calls_by_day.length, false, renderCallsSvg(d.happened.calls_by_day, d.today.quota)),
    panel("p-counts", 3, "Runs by agent", agents.length, false, `<div class="wrap">${renderTable(["agent", ...statuses], countRows, statuses.map((_, i) => i + 1))}</div>`),
    panel("p-runs", 3, "Runs", d.happened.runs.length, false, `${runFilters}<div class="wrap">${renderTable(["dispatched (UTC)", "agent", "subject", "trigger", "outcome", "took", "calls", ""], runRows, [5, 6], runRowAttrs)}</div>`),
  ].join("\n");

  const statusDot = `<svg class="brand-dot ${d.paused ? "is-paused" : "is-live"}" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="currentColor" opacity="0.2"/><circle cx="6" cy="6" r="3" fill="currentColor"/></svg>`;

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Factory dashboard</title>
${BOOT_SCRIPT}
<style>
:root{color-scheme:light;${tokens(NEXT_THEMES.light)}}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;${tokens(NEXT_THEMES.dark)}}}
:root[data-theme="dark"]{color-scheme:dark;${tokens(NEXT_THEMES.dark)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:18px;font-weight:600;letter-spacing:-0.015em;line-height:1.3;margin:0;display:flex;align-items:center;gap:8px}
h2{font-size:15px;font-weight:600;letter-spacing:-0.01em;margin:0}
h3{font-size:13.5px;font-weight:600;margin:0}
code,.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
code{font-size:12px}
.ico{width:12px;height:12px;vertical-align:-1.5px;flex:none}
.brand-dot{width:12px;height:12px;flex:none}.brand-dot.is-live{color:var(--good)}.brand-dot.is-paused{color:var(--bad)}

.hdr{position:sticky;top:0;z-index:10;background:var(--bg);background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.hdr-in{max-width:1280px;margin:0 auto;padding:10px 24px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.hdr nav{display:flex;flex-wrap:wrap;gap:2px 10px;font-size:13px}
.hdr nav a{color:var(--fg2);padding:3px 6px;border-radius:6px}
.hdr nav a:hover{color:var(--fg);background:var(--card);text-decoration:none}
.ui-seg{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:7px;background:var(--card);padding:2px;font-size:12px;gap:2px}
.ui-seg .seg-on{background:var(--elev);color:var(--fg);font-weight:600;padding:2px 8px;border-radius:5px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}
.ui-seg a{color:var(--fg2);padding:2px 8px;border-radius:5px}
.ui-seg a:hover{color:var(--fg);text-decoration:none}
.ctl{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px;color:var(--fg2);margin-left:auto}.ctl[hidden]{display:none}
.ctl select,.ctl button{font:inherit;color:var(--fg);background:var(--elev);border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer}
.ctl button:hover,.ctl select:hover{border-color:var(--fg2)}
.ar-lbl{display:inline-flex;align-items:center;gap:4px;cursor:pointer}

.shell{max-width:1280px;margin:0 auto;padding:0 24px 32px}
.meta{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;color:var(--fg2);font-size:12.5px;padding:12px 0 4px}
.repo-badge{display:inline-flex;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:1px 8px;font-size:12px}
.win-seg{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:6px;background:var(--card);padding:1px;gap:1px}
.win-seg a{color:var(--fg2);padding:1px 7px;border-radius:5px;font-size:12px}
.win-seg a:hover{color:var(--fg);text-decoration:none}
.win-seg a.win-on{background:var(--elev);color:var(--fg);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,0.05)}

.alert{margin:10px 0 0;padding:10px 14px;border-radius:8px;border:1px solid var(--badLine);background:var(--badBg);color:var(--bad);font-size:13px;display:flex;align-items:flex-start;gap:8px}
.callout{margin:10px 0 0;padding:8px 14px;border-radius:8px;border:1px solid var(--badLine);background:var(--badBg);color:var(--bad);font-size:13px;display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.callout a{color:var(--bad);font-weight:600;text-decoration:underline;margin-left:auto}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:10px;margin:14px 0 18px}
.tile{background:var(--elev);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.tile-alert{border-color:var(--badLine);border-left:3px solid var(--bad)}
.tile .label{color:var(--fg2);font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;display:flex;align-items:center;gap:6px}
.tile .value{font-size:26px;font-weight:600;letter-spacing:-0.02em;line-height:1.2;margin:6px 0 4px;font-variant-numeric:tabular-nums}
.tile .sub{color:var(--fg2);font-size:12px}
.quota-den{font-size:16px;font-weight:500}
.bad-txt{color:var(--bad);font-weight:500}
.pulse-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--good);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.85)}}
@media (prefers-reduced-motion:reduce){.pulse-dot{animation:none}}

.meter{height:6px;border-radius:99px;background:var(--line2);margin:6px 0 5px;display:flex;overflow:hidden}
.m-used{height:100%}.m-res{height:100%;background:repeating-linear-gradient(-45deg,var(--muteLine),var(--muteLine) 3px,transparent 3px,transparent 6px)}

details.panel{margin:0}
details.p2{border-top:1px solid var(--line);padding:14px 0 6px;scroll-margin-top:64px}
details.p3{margin:12px 0 6px}
details.panel>summary{cursor:pointer;list-style-position:outside;margin-left:16px;padding:2px 0}
details.panel>summary h2,details.panel>summary h3{display:inline}
.count{color:var(--fg2);font-weight:400}
.pbody{padding:10px 0 4px}

.filter-bar,.run-filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.filter-bar[hidden],.run-filters[hidden]{display:none}
.chip{font:inherit;font-size:12px;color:var(--fg2);background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 10px;cursor:pointer}
.chip:hover{color:var(--fg);border-color:var(--fg2)}
.chip.active{background:var(--accentBg);color:var(--accent);border-color:var(--accent);font-weight:600}

.blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:12px;align-items:start}
.blk{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.blk[hidden]{display:none}
.blk-you{border-left:3px solid var(--bad)}
.blk-wait{border-left:3px solid var(--muteLine)}
.blk h3{margin:0 0 8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.count-badge{background:var(--card2);border:1px solid var(--line);border-radius:99px;padding:0 7px;font-size:11.5px;color:var(--fg2);font-weight:500}
.blk-shared{margin:0 0 8px;font-size:12.5px}
.item{padding:10px 0;border-top:1px solid var(--line2);display:flex;flex-direction:column;gap:5px}
.item-head{font-size:13.5px;line-height:1.4}
.todo{font-size:13px;padding:5px 8px;border-radius:6px;border:1px solid var(--line2);background:var(--elev)}
.todo-you{border-color:var(--badLine);background:var(--badBg);color:var(--fg)}
.reason{font-size:12.5px}
.cmd-wrap{position:relative;background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:6px 10px;display:flex;align-items:flex-start;gap:8px}
code.act{white-space:pre-wrap;word-break:break-all;display:block;color:var(--fg2);flex:1;min-width:0}
.copy-btn{font:inherit;font-size:11px;color:var(--fg2);background:var(--elev);border:1px solid var(--line);border-radius:5px;padding:2px 7px;cursor:pointer;flex:none}
.copy-btn:hover{color:var(--fg);border-color:var(--fg2)}
.copy-btn.copied{color:var(--good);border-color:var(--goodLine);background:var(--goodBg)}
.copy-btn[hidden]{display:none}

.wrap{overflow:auto;max-height:70vh;border:1px solid var(--line);border-radius:8px;background:var(--elev)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;vertical-align:top;padding:7px 10px;border-bottom:1px solid var(--line2)}
th{color:var(--fg2);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:0.03em;position:sticky;top:0;z-index:1;background:var(--card);border-bottom:1px solid var(--line)}
tbody tr:hover{background:var(--card)}
tbody tr[hidden]{display:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.zero{color:var(--fg2);opacity:0.45}
.trunc{display:inline-block;max-width:min(40ch,100%);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}

.pill{display:inline-flex;align-items:center;gap:4px;padding:1px 8px;border-radius:99px;font-size:11.5px;font-weight:500;white-space:nowrap;border:1px solid transparent}
.good{color:var(--good);background:var(--goodBg);border-color:var(--goodLine)}
.bad{color:var(--bad);background:var(--badBg);border-color:var(--badLine)}
.warn{color:var(--warn);background:var(--warnBg);border-color:var(--warnLine)}
.mute{color:var(--mute);background:var(--muteBg);border-color:var(--muteLine)}
.accent-pill{color:var(--accent);background:var(--accentBg);border-color:var(--accent)}

.dim{color:var(--fg2)}.hint{font-size:12px;color:var(--fg2);font-style:italic}.empty{color:var(--fg2);font-style:italic;margin:4px 0}p{margin:0 0 8px}
.queues{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:12px;margin-bottom:8px}
.q{background:var(--elev);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.q-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}
.q-lead{font-size:13px;padding:5px 8px;border-radius:6px;background:var(--card);border:1px solid var(--line2);margin-bottom:6px}
.q-lead-go{background:var(--accentBg);border-color:var(--accent)}
.q ol{margin:4px 0 0;padding-left:20px}.q li{margin:3px 0}.q li.held{color:var(--fg2)}

.chart-wrap{background:var(--elev);border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow-x:auto}
.calls-svg{display:block;width:100%;max-width:740px;height:auto}
#p-decisions{margin:12px 0}#p-decisions>summary{cursor:pointer;color:var(--fg2);font-size:13px}
footer{margin-top:20px;padding-top:12px;border-top:1px solid var(--line2);font-size:12px}

:root[data-density="compact"] .tiles{gap:7px;margin:10px 0 12px}
:root[data-density="compact"] .tile{padding:9px 11px;border-radius:8px}
:root[data-density="compact"] .tile .value{font-size:21px;margin:2px 0}
:root[data-density="compact"] details.p2{padding:9px 0 4px}
:root[data-density="compact"] .blk{padding:8px 10px;border-radius:8px}
:root[data-density="compact"] .item{padding:6px 0;gap:3px}
:root[data-density="compact"] .q{padding:8px 10px;border-radius:8px}
:root[data-density="compact"] th,:root[data-density="compact"] td{padding:4px 7px}

@media (max-width:700px){
  .hdr{position:static}
  .hdr-in{padding:10px 14px}
  .shell{padding:0 14px 24px}
  .ctl{margin-left:0}
}
</style></head><body>
<header class="hdr"><div class="hdr-in">
<h1>${statusDot}<span>Factory dashboard</span>${d.paused ? ` <span class="pill bad">${SVG_X} PAUSED</span>` : ""}</h1>
<nav aria-label="Sections">${nav}</nav>
<div class="ui-seg" role="group" aria-label="Dashboard UI version"><span class="seg-on" aria-current="page">New UI</span><a id="ui-toggle" href="${dashboardHref(d.window_hours, "old")}">Old UI</a></div>
${controls}
</div></header>
<main class="shell">
<div class="meta">
<span class="repo-badge mono">${link(`https://github.com/${repo}`, repo)}</span>
<span>Generated ${esc(d.generated_at.slice(0, 19).replace("T", " "))} UTC</span>
<div class="win-seg" role="group" aria-label="Time window">${windowLinks}</div>
<span><a href="?format=json&hours=${d.window_hours}">JSON</a> &middot; <a href="/status">Status</a></span>
</div>
${d.github_error ? `<div class="alert" role="alert">${SVG_ALERT}<span><b>GitHub unreachable:</b> ${esc(d.github_error)}. Issue, PR and queue sections are empty; the run ledger is still live.</span></div>` : ""}
${human.length > 0 ? `<div class="callout">${SVG_ALERT}<span><b>${human.length} ${human.length === 1 ? "item needs" : "items need"} your action</b></span><a href="#p-blocked">Jump to blocked &rarr;</a></div>` : ""}
<div class="tiles">${tiles}</div>
${panel("p-blocked", 2, "Blocked: what needs a human, and what is just waiting", d.blocked.length, true, blockedBody)}
${panel("p-flight", 2, "In flight", d.in_flight.length, true, `<div class="wrap">${renderTable(["agent", "subject", "status", "dispatched (UTC)", ""], flightRows)}</div>`)}
${panel("p-next", 2, "Next: what the tick will do", queued, true, nextBody)}
${panel("p-happened", 2, `Happened: last ${d.window_hours} h`, d.happened.runs.length, true, happenedBody)}
<footer class="dim">Read-only. Times are UTC. With JavaScript on, the page reloads every ${AUTO_REFRESH_SECONDS} s while auto-refresh is ticked (the default; your choice, theme, density, UI version and open panels are kept in this browser); without it, reload by hand.</footer>
</main>
${PAGE_SCRIPT}
</body></html>`;
}
