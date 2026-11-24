import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "honeycomb-board.sh"
BOARD_DIR = ROOT / "docs" / "honeycomb"
PANELS = sorted((BOARD_DIR / "panels").glob("*.json"))
QUERY_PANELS = [p for p in PANELS if json.loads(p.read_text()).get("type", "query") == "query"]
HARNESS_PANELS = ("08", "09", "10", "12", "13", "14", "21", "22", "23", "24", "25", "27", "28", "29", "30")
PANEL_NAMES = ["00-runbook", "01-success-rate", "02-retry-loops", "03-run-outcomes", "04-where-runs-die",
               "05-quota-burn", "06-run-time", "07-model-calls-vs-cap", "08-model-reliability", "09-model-switches",
               "10-model-retries", "11-throttles", "12-tokens", "13-tool-guard-blocked", "14-guard-events",
               "15-pr-acceptance", "16-pr-outcomes", "17-review-verdicts", "18-tick-decisions",
               "19-prs-opened", "20-merges-per-day", "21-tool-result-tokens", "22-tool-result-size",
               "23-prompt-size", "24-tool-batching", "25-re-reads", "26-paid-spend", "27-prompt-makeup",
               "28-input-by-request", "29-prompt-sections", "30-cached-input"]
COLORS = {"red", "yellow", "blue", "green", "purple"}
LINE_STYLES = {"filled-solid", "filled-dotted", "solid", "dotted"}

QUERY_OPS = {"COUNT", "CONCURRENCY", "SUM", "AVG", "COUNT_DISTINCT", "HEATMAP", "MAX", "MIN", "P001", "P01", "P05",
             "P10", "P20", "P25", "P50", "P75", "P80", "P90", "P95", "P99", "P999", "RATE_AVG", "RATE_SUM", "RATE_MAX"}
FILTER_OPS = {"=", "!=", ">", ">=", "<", "<=", "starts-with", "does-not-start-with", "ends-with", "does-not-end-with",
              "exists", "does-not-exist", "contains", "does-not-contain", "in", "not-in"}


def _env(**extra):
    e = {k: v for k, v in os.environ.items() if k not in ("HONEYCOMB_CONFIG_KEY", "HONEYCOMB_BOARD_DIR")}
    e.update(extra)
    return e


def _requests(stdout):
    lines = stdout.splitlines()
    return [(lines[i].split(" ", 1)[0], lines[i].split(" ", 1)[1],
             json.loads(lines[i + 1]) if i + 1 < len(lines) and lines[i + 1].startswith("{") else None)
            for i in range(len(lines)) if lines[i][:1].isupper()]


def test_dry_run_sequence_without_key():
    r = subprocess.run([str(SCRIPT), "--dry-run"], env=_env(), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    reqs = _requests(r.stdout)
    cols = json.loads((BOARD_DIR / "derived_columns.json").read_text())
    expected = [("GET", "/1/columns/factory"), ("GET", "/1/derived_columns/factory"),
                ("GET", "/1/columns/software-factory"), ("GET", "/1/derived_columns/software-factory")]
    board_id = json.loads((BOARD_DIR / "board.json").read_text())["id"]
    expected.append(("GET", f"/1/boards/{board_id}") if board_id else ("GET", "/1/boards"))
    for c in cols:
        expected.append(("POST", f"/1/derived_columns/{c['dataset']}"))
    datasets = []
    for path in QUERY_PANELS:
        ds = json.loads(path.read_text())["dataset"]
        datasets.append(ds)
        expected += [("POST", f"/1/queries/{ds}"), ("POST", f"/1/query_annotations/{ds}")]
    expected += [("PUT", f"/1/boards/{board_id}")] if board_id else [("POST", "/1/boards")]
    expected += [("GET", f"/1/query_annotations/{ds}") for ds in sorted(set(datasets))]
    assert len(cols) == 7 and len(QUERY_PANELS) == 30
    assert [(m, p) for m, p, _ in reqs] == expected
    board = next(b for m, p, b in reqs if p.startswith("/1/boards") and m in ("PUT", "POST"))
    assert len(board["panels"]) == len(PANELS)
    assert board["panels"][0]["type"] == "text" and board["panels"][0]["text_panel"]["content"]
    first = board["panels"][1]["query_panel"]
    assert first["query_id"] == f"<query:{QUERY_PANELS[0].stem}>"
    assert first["query_annotation_id"] == f"<annotation:{QUERY_PANELS[0].stem}>"


def test_real_run_needs_key():
    r = subprocess.run([str(SCRIPT)], env=_env(), capture_output=True, text=True)
    assert r.returncode != 0
    assert "HONEYCOMB_CONFIG_KEY" in r.stderr


def test_board_json_shape():
    b = json.loads((BOARD_DIR / "board.json").read_text())
    assert b["name"] == "factory" and b["type"] == "flexible" and b["layout_generation"] == "manual"
    assert 1 <= len(b["preset_filters"]) <= 5
    assert {p["column"] for p in b["preset_filters"]} == {"agent_d", "dry_run"}
    assert b["id"] is None or isinstance(b["id"], str)
    cols = json.loads((BOARD_DIR / "derived_columns.json").read_text())
    assert {c["alias"] for c in cols} == {"agent_d", "calls_ratio", "run_ok", "model_d", "pr_merged", "pr_first_pass", "reason_d"}
    assert all(set(c) == {"dataset", "alias", "expression", "description"} for c in cols)
    assert {c["alias"]: c["dataset"] for c in cols}["model_d"] == "software-factory"
    assert "$factory.agent" not in {c["alias"]: c for c in cols}["agent_d"]["expression"]


def test_panel_names_match_contract():
    assert [p.stem for p in PANELS] == PANEL_NAMES


def _check_position(pos):
    assert set(pos) == {"x_coordinate", "y_coordinate", "width", "height"}
    assert pos["x_coordinate"] >= 0 and pos["y_coordinate"] >= 0 and pos["width"] >= 1 and pos["height"] >= 1
    assert pos["x_coordinate"] + pos["width"] <= 12


def test_text_panel():
    p = json.loads((BOARD_DIR / "panels" / "00-runbook.json").read_text())
    assert p["type"] == "text" and p["content"].strip()
    assert set(p) == {"type", "content", "position"}
    _check_position(p["position"])


@pytest.mark.parametrize("path", QUERY_PANELS, ids=lambda p: p.stem)
def test_panel_static(path):
    p = json.loads(path.read_text())
    assert {"dataset", "name", "description", "query", "query_style", "position"} <= set(p)
    assert p["dataset"] in ("factory", "software-factory")
    assert p["dataset"] == ("software-factory" if path.name[:2] in HARNESS_PANELS else "factory")
    assert 0 < len(p["name"]) <= 320 and 0 < len(p["description"]) <= 1023
    assert p["query_style"] in ("graph", "table", "combo")
    q = p["query"]
    assert "breakdowns" in q and isinstance(q["breakdowns"], list)
    assert q["calculations"] and all(c["op"] in QUERY_OPS for c in q["calculations"])
    for c in q["calculations"]:
        assert (c["op"] == "COUNT") == ("column" not in c)
    for f in q.get("filters", []):
        assert f["op"] in FILTER_OPS
        assert ("value" not in f) == (f["op"] in ("exists", "does-not-exist"))
        if f["op"] in ("in", "not-in"):
            assert isinstance(f["value"], list)
    assert q.get("filter_combination", "AND") in ("AND", "OR")
    calc_terms = {c.get("column") or c["op"] for c in q["calculations"]} | {c["op"] for c in q["calculations"]}
    terms = set(q["breakdowns"]) | calc_terms
    for o in q.get("orders", []):
        assert o["order"] in ("ascending", "descending")
        assert (o.get("column") or o["op"]) in terms
    for h in q.get("havings", []):
        assert h["calculate_op"] in QUERY_OPS and h["op"] in FILTER_OPS and "value" in h
        assert h["calculate_op"] in {c["op"] for c in q["calculations"]}
    tr = q["time_range"]
    assert tr in (86400, 604800, 2592000)
    if "granularity" in q:
        assert tr / 1000 <= q["granularity"] <= tr
    assert q.get("limit", 1) <= 1000
    vs = p.get("visualization_settings", {})
    for chart in vs.get("charts", []):
        assert 0 <= chart["chart_index"] < len(q["calculations"])
        assert len(chart.get("thresholds", [])) <= 5
        for t in chart.get("thresholds", []):
            assert set(t) <= {"value", "color", "operation", "line_style", "label"}
            assert isinstance(t["value"], (int, float))
            assert t["color"] in COLORS and t["operation"] in ("gt", "lt") and t["line_style"] in LINE_STYLES
            assert len(t.get("label", "")) <= 35
    _check_position(p["position"])


def test_panels_do_not_overlap():
    rects = []
    for path in PANELS:
        pos = json.loads(path.read_text())["position"]
        rects.append((path.stem, pos["x_coordinate"], pos["y_coordinate"],
                      pos["x_coordinate"] + pos["width"], pos["y_coordinate"] + pos["height"]))
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], f"{a[0]} overlaps {b[0]}"


STUB = r"""#!/usr/bin/env bash
# Stateful Honeycomb stub: state files live in $STUB_STATE (columns-<ds>.json, derived-<ds>.json,
# annotations-<ds>.json, boards.json, board-<id>.json); every request is appended to $STUB_LOG.
cat >/dev/null
method=GET; data=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method=$2; shift ;;
    --data) data=$2; shift ;;
    http*) url=$1 ;;
  esac
  shift
done
path="${url#https://api.eu1.honeycomb.io}"
printf '%s %s %s\n' "$method" "$path" "$data" >> "$STUB_LOG"
st="$STUB_STATE"
n=$(( $(cat "$st/counter" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$st/counter"
read_or() { if [ -f "$1" ]; then cat "$1"; else printf '%s' "$2"; fi; }
push() { jq -c --argjson x "$3" ". + [\$x]" <<<"$(read_or "$1" '[]')" > "$1.tmp" && mv "$1.tmp" "$1"; }
IFS=/ read -r _ _ kind ds id <<<"${path%%\?*}"
body='{"error":"unexpected"}'; code=500
case "$method $kind" in
  "GET columns")
    if [ -f "$st/columns-$ds.json" ]; then body=$(cat "$st/columns-$ds.json"); code=200; else body='{}'; code=404; fi ;;
  "GET derived_columns") body=$(read_or "$st/derived-$ds.json" '[]'); code=200 ;;
  "POST derived_columns") push "$st/derived-$ds.json" x "$(jq -c ". + {id: \"dc$n\"}" <<<"$data")"; body="{\"id\":\"dc$n\"}"; code=201 ;;
  "PUT derived_columns")
    jq -c --arg i "$id" --argjson d "$data" 'map(if .id == $i then . + $d else . end)' <<<"$(read_or "$st/derived-$ds.json" '[]')" > "$st/derived-$ds.json.tmp" \
      && mv "$st/derived-$ds.json.tmp" "$st/derived-$ds.json"; body='{}'; code=200 ;;
  "POST queries") body="{\"id\":\"q$n\"}"; code=200 ;;
  "GET query_annotations") body=$(read_or "$st/annotations-$ds.json" '[]'); code=200 ;;
  "POST query_annotations")
    push "$st/annotations-$ds.json" x "$(jq -c --arg i "a$n" '{id: $i, name}' <<<"$data")"; body="{\"id\":\"a$n\"}"; code=201 ;;
  "DELETE query_annotations")
    jq -c --arg i "$id" 'map(select(.id != $i))' <<<"$(read_or "$st/annotations-$ds.json" '[]')" > "$st/annotations-$ds.json.tmp" \
      && mv "$st/annotations-$ds.json.tmp" "$st/annotations-$ds.json"; body=''; code=204 ;;
  "GET boards")
    if [ -z "$ds" ]; then body=$(read_or "$st/boards.json" '[]'); code=200
    elif [ -f "$st/board-$ds.json" ]; then body=$(cat "$st/board-$ds.json"); code=200; else body='{}'; code=404; fi ;;
  "POST boards")
    echo "$data" > "$st/board-b-new$n.json"; push "$st/boards.json" x "$(jq -c --arg i "b-new$n" '{id: $i, name}' <<<"$data")"
    body="{\"id\":\"b-new$n\",\"links\":{\"board_url\":\"https://ui.example/b-new$n\"}}"; code=201 ;;
  "PUT boards")
    if [ -f "$st/board-$ds.json" ]; then echo "$data" > "$st/board-$ds.json"; body="{\"id\":\"$ds\",\"links\":{\"board_url\":\"https://ui.example/$ds\"}}"; code=200
    else body='{}'; code=404; fi ;;
esac
printf '%s\n%s' "$body" "$code"
"""

FACTORY_COLS = ["name", "run_id", "agent", "subject", "phase", "status", "stop_reason", "error", "workflow_run_url",
                "model_calls", "reserved_calls", "throttles", "duration_s", "queue_s", "dry_run", "outcome", "autonomy",
                "verdict", "kind", "reason", "merged", "paid_cost_usd"]
HARNESS_COLS = ["name", "factory.agent", "factory.phase", "factory.model_id", "gen_ai.usage.input_tokens",
                "gen_ai.usage.output_tokens", "factory.model.from", "factory.model.to", "factory.model.reason",
                "factory.retry.reason", "rule", "guard", "gen_ai.request.model", "factory.tool.name",
                "factory.tool.target", "factory.tool.result_tokens", "factory.run_id",
                "gen_ai.usage.cache_read.input_tokens", "factory.prompt.request", "factory.prompt.total_tokens",
                "factory.prompt.history_tokens", "factory.task.part", "factory.task.section",
                "factory.task.section_tokens"] + [f"factory.prompt.{p}_tokens" for p in
                                                  ("system", "tools", "task", "tool_results", "assistant_text",
                                                   "tool_args", "edit_args")]
WRITES = ("POST", "PUT", "DELETE")


class Stub:
    """A temp copy of docs/honeycomb, a stub curl on PATH and the stub's state directory."""

    def __init__(self, tmp_path, board_id=None, cols_f=FACTORY_COLS, cols_s=HARNESS_COLS):
        self.dir = tmp_path / "hc"
        shutil.copytree(BOARD_DIR, self.dir)
        board = json.loads((self.dir / "board.json").read_text())
        board["id"] = board_id
        (self.dir / "board.json").write_text(json.dumps(board))
        self.state = tmp_path / "state"
        self.state.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        curl = bin_dir / "curl"
        curl.write_text(STUB)
        curl.chmod(0o755)
        self.log = tmp_path / "log"
        self.log.write_text("")
        for ds, cols in (("factory", cols_f), ("software-factory", cols_s)):
            if cols is not None:
                self.put(f"columns-{ds}.json", [{"key_name": c} for c in cols])
        self.env = _env(PATH=f"{bin_dir}:{os.environ['PATH']}", HONEYCOMB_CONFIG_KEY="k",
                        HONEYCOMB_BOARD_DIR=str(self.dir), STUB_LOG=str(self.log), STUB_STATE=str(self.state))

    def put(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def get(self, name):
        return json.loads((self.state / name).read_text())

    def run(self):
        self.log.write_text("")
        r = subprocess.run([str(SCRIPT)], env=self.env, capture_output=True, text=True)
        calls = [line.split(" ", 2) for line in self.log.read_text().splitlines()]
        return r, calls

    def board_id(self):
        return json.loads((self.dir / "board.json").read_text())["id"]


def _writes(calls):
    return [(c[0], c[1]) for c in calls if c[0] in WRITES]


def _seq(calls):
    return [(c[0], c[1]) for c in calls]


def _panel_posts(calls):
    return [c for c in calls if c[0] == "POST" and c[1].startswith("/1/query_annotations/")]


def _derived_posts(calls):
    return [json.loads(c[2])["alias"] for c in calls if c[0] == "POST" and c[1].startswith("/1/derived_columns/")]


def _panel_board(name="factory"):
    return {"name": name, "panels": []}


def test_update_path_replaces_board_and_upserts_columns(tmp_path):
    s = Stub(tmp_path, "b1")
    s.put("board-b1.json", _panel_board())
    cols = {c["alias"]: c for c in json.loads((BOARD_DIR / "derived_columns.json").read_text())}
    s.put("derived-factory.json", [{"id": "dc1", **{k: v for k, v in cols["agent_d"].items() if k != "dataset"}},
                                  {"id": "dc2", **{k: v for k, v in cols["calls_ratio"].items() if k != "dataset"}, "description": "stale"}])
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    seq = _seq(calls)
    assert ("PUT", "/1/derived_columns/factory/dc2") in seq  # description differs
    assert ("PUT", "/1/derived_columns/factory/dc1") not in seq  # unchanged
    assert sorted(_derived_posts(calls)) == ["model_d", "pr_first_pass", "pr_merged", "reason_d", "run_ok"]
    assert ("POST", "/1/boards") not in seq
    assert len([1 for m, p in seq if m == "POST" and p.startswith("/1/queries/")]) == len(QUERY_PANELS)
    assert len(_panel_posts(calls)) == len(QUERY_PANELS)
    put = json.loads(next(c[2] for c in calls if c[0] == "PUT" and c[1] == "/1/boards/b1"))
    assert put["id"] == "b1" and len(put["panels"]) == len(PANELS) and put["panels"][0]["type"] == "text"
    assert all("dataset" not in x["query_panel"] for x in put["panels"] if x["type"] == "query")
    assert "https://ui.example/b1" in r.stdout
    assert s.board_id() == "b1"


def test_alias_colliding_with_real_column_dies_before_any_write(tmp_path):
    s = Stub(tmp_path, "b1", cols_f=FACTORY_COLS + ["run_ok"])
    s.put("board-b1.json", _panel_board())
    r, calls = s.run()
    assert r.returncode != 0
    assert "run_ok" in r.stderr and "real column" in r.stderr
    assert _writes(calls) == []


def test_identical_derived_column_is_not_written(tmp_path):
    s = Stub(tmp_path, "b1")
    s.put("board-b1.json", _panel_board())
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    first = _derived_posts(calls)
    assert sorted(first) == ["agent_d", "calls_ratio", "model_d", "pr_first_pass", "pr_merged", "reason_d", "run_ok"]
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert not [c for c in calls if c[1].startswith("/1/derived_columns/") and c[0] in WRITES]


def test_board_found_by_name_is_reused_and_id_written_back(tmp_path):
    s = Stub(tmp_path, None)
    s.put("boards.json", [{"id": "other", "name": "not ours"}, {"id": "b7", "name": "factory"}])
    s.put("board-b7.json", _panel_board())
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    seq = _seq(calls)
    assert ("PUT", "/1/boards/b7") in seq and ("POST", "/1/boards") not in seq
    assert s.board_id() == "b7"
    # Second run: the id is now in board.json, so it is read by id and replaced again, nothing created.
    r, calls = s.run()
    assert ("GET", "/1/boards/b7") in _seq(calls) and ("POST", "/1/boards") not in _seq(calls)


def test_two_boards_with_the_name_die_before_any_write(tmp_path):
    s = Stub(tmp_path, None)
    s.put("boards.json", [{"id": "b1", "name": "factory"}, {"id": "b2", "name": "factory"}])
    r, calls = s.run()
    assert r.returncode != 0
    assert "b1" in r.stderr and "b2" in r.stderr
    assert _writes(calls) == []


def test_board_id_set_but_missing_dies_before_any_write(tmp_path):
    s = Stub(tmp_path, "gone")
    r, calls = s.run()
    assert r.returncode != 0
    assert "board gone not found; set id to null in board.json to create it" in r.stderr
    assert _writes(calls) == []


def test_no_board_creates_it_and_writes_id(tmp_path):
    s = Stub(tmp_path, None)
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert ("POST", "/1/boards") in _seq(calls)
    assert s.board_id().startswith("b-new")
    assert json.loads((BOARD_DIR / "board.json").read_text())["id"] != s.board_id()


def test_cleanup_deletes_only_our_stale_annotations(tmp_path):
    s = Stub(tmp_path, "b1")
    s.put("board-b1.json", _panel_board())
    stale = json.loads(PANELS[1].read_text())["name"]  # "Success rate", a name of ours
    s.put("annotations-factory.json", [{"id": "stale1", "name": stale}, {"id": "foreign1", "name": "Someone else's"}])
    s.put("annotations-software-factory.json", [{"id": "stale2", "name": json.loads(
        (BOARD_DIR / "panels" / "08-model-reliability.json").read_text())["name"]}])
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    deletes = sorted(c[1] for c in calls if c[0] == "DELETE")
    assert deletes == ["/1/query_annotations/factory/stale1", "/1/query_annotations/software-factory/stale2"]
    left = {a["id"] for a in s.get("annotations-factory.json")}
    assert "foreign1" in left and "stale1" not in left
    # The annotations just created for the board survive: one per query panel in the stub state.
    new_ids = {a["id"] for ds in ("factory", "software-factory") for a in s.get(f"annotations-{ds}.json")} - {"foreign1"}
    assert len(new_ids) == len(QUERY_PANELS)


def test_second_run_converges(tmp_path):
    s = Stub(tmp_path, None)
    r, _ = s.run()
    assert r.returncode == 0, r.stderr
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert not [c for c in calls if c[1].startswith("/1/derived_columns/") and c[0] in WRITES]
    assert not [c for c in calls if (c[0], c[1]) == ("POST", "/1/boards")]
    # One annotation per query panel remains: the first run's were deleted after the second run's board write.
    total = sum(len(s.get(f"annotations-{ds}.json")) for ds in ("factory", "software-factory"))
    assert total == len(QUERY_PANELS)


def test_failed_request_names_it(tmp_path):
    s = Stub(tmp_path, "b1")
    s.put("board-b1.json", _panel_board())
    (s.state / "board-b1.json").unlink()
    r, calls = s.run()
    assert r.returncode != 0 and "b1" in r.stderr


def test_missing_column_skips_derived_column_and_panels(tmp_path):
    s = Stub(tmp_path, None, cols_f=[c for c in FACTORY_COLS if c != "reserved_calls"])
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert "skipping derived column calls_ratio" in r.stderr and "reserved_calls" in r.stderr
    assert "skipping panel 07-model-calls-vs-cap" in r.stderr
    assert _derived_posts(calls) == ["agent_d", "run_ok", "model_d", "pr_merged", "pr_first_pass", "reason_d"]
    assert len(_panel_posts(calls)) == len(QUERY_PANELS) - 1
    assert ("POST", "/1/boards") in _seq(calls)
    assert f"applied {len(QUERY_PANELS) - 1} of {len(QUERY_PANELS)} panels" in r.stderr


def test_missing_harness_dataset_skips_harness_panels(tmp_path):
    s = Stub(tmp_path, None, cols_s=None)
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    harness = [p for p in QUERY_PANELS if json.loads(p.read_text())["dataset"] == "software-factory"]
    assert harness
    for p in harness:
        assert f"skipping panel {p.stem}" in r.stderr
    assert not [c for c in calls if c[1].startswith("/1/queries/software-factory")]
    assert len(_panel_posts(calls)) == len(QUERY_PANELS) - len(harness)
    assert ("POST", "/1/boards") in _seq(calls)


def test_no_applicable_panel_leaves_board_alone(tmp_path):
    s = Stub(tmp_path, "b1", cols_f=[], cols_s=[])
    s.put("board-b1.json", _panel_board())
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert f"applied 0 of {len(QUERY_PANELS)} panels" in r.stderr
    assert not [c for c in calls if "/boards" in c[1] and c[0] in WRITES]
    assert s.board_id() == "b1"


def test_missing_model_column_skips_model_d_and_reliability_panel(tmp_path):
    s = Stub(tmp_path, None, cols_s=[c for c in HARNESS_COLS if c != "gen_ai.request.model"])
    r, calls = s.run()
    assert r.returncode == 0, r.stderr
    assert "skipping derived column model_d" in r.stderr and "gen_ai.request.model" in r.stderr
    assert "skipping panel 08-model-reliability" in r.stderr and "model_d" in r.stderr
    assert "model_d" not in _derived_posts(calls)
    assert len(_panel_posts(calls)) == len(QUERY_PANELS) - 1
    board = json.loads(next(c[2] for c in calls if (c[0], c[1]) == ("POST", "/1/boards")))
    assert board["panels"][0]["type"] == "text"
