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
    expected = [("GET", "/1/columns/factory"), ("GET", "/1/columns/software-factory")]
    for c in cols:
        expected += [("GET", f"/1/derived_columns/factory?alias={c['alias']}"), ("POST", "/1/derived_columns/factory")]
    for path in PANELS:
        ds = json.loads(path.read_text())["dataset"]
        expected += [("POST", f"/1/queries/{ds}"), ("POST", f"/1/query_annotations/{ds}")]
    expected.append(("POST", "/1/boards"))
    assert [(m, p) for m, p, _ in reqs] == expected
    board = reqs[-1][2]
    assert len(board["panels"]) == len(PANELS)
    assert board["panels"][0]["query_panel"]["query_id"] == f"<query:{PANELS[0].stem}>"
    assert board["panels"][0]["query_panel"]["query_annotation_id"] == f"<annotation:{PANELS[0].stem}>"
    assert "id" not in board


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
    assert {c["alias"] for c in cols} == {"agent_d", "calls_ratio"}
    assert all(set(c) == {"dataset", "alias", "expression", "description"} and c["dataset"] == "factory" for c in cols)
    assert "$factory.agent" not in {c["alias"]: c for c in cols}["agent_d"]["expression"]


@pytest.mark.parametrize("path", PANELS, ids=lambda p: p.stem)
def test_panel_static(path):
    p = json.loads(path.read_text())
    assert {"dataset", "name", "description", "query", "query_style", "position"} <= set(p)
    assert p["dataset"] in ("factory", "software-factory")
    harness = path.name[:2] in ("06", "07", "08", "10", "11")
    assert p["dataset"] == ("software-factory" if harness else "factory")
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
    terms = set(q["breakdowns"]) | {c.get("column") or c["op"] for c in q["calculations"]} | \
        {c["op"] for c in q["calculations"]}
    for o in q.get("orders", []):
        assert o["order"] in ("ascending", "descending")
        assert (o.get("column") or o["op"]) in terms
    tr = q["time_range"]
    assert tr == 604800
    if "granularity" in q:
        assert tr / 1000 <= q["granularity"] <= tr
    assert q.get("limit", 1) <= 1000
    pos = p["position"]
    assert set(pos) == {"x_coordinate", "y_coordinate", "width", "height"}
    assert pos["x_coordinate"] >= 0 and pos["y_coordinate"] >= 0 and pos["width"] >= 1 and pos["height"] >= 1
    assert pos["x_coordinate"] + pos["width"] <= 12


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
case "$method $path" in
  "GET /1/columns/factory") body="$STUB_COLS_F"; code=200 ;;
  "GET /1/columns/software-factory") body="$STUB_COLS_S"; code=${STUB_COLS_S_CODE:-200} ;;
  "GET /1/derived_columns/factory?alias=agent_d") body="$STUB_AGENT_D"; code=${STUB_AGENT_D_CODE:-200} ;;
  "GET /1/derived_columns/factory?alias=calls_ratio") body="$STUB_CALLS"; code=${STUB_CALLS_CODE:-200} ;;
  "POST /1/derived_columns/factory") body='{"id":"dc-new"}'; code=201 ;;
  "PUT /1/derived_columns/"*) body='{}'; code=200 ;;
  "POST /1/queries/"*) n=$(wc -l < "$STUB_LOG"); body="{\"id\":\"q$n\"}"; code=200 ;;
  "POST /1/query_annotations/"*) n=$(wc -l < "$STUB_LOG"); body="{\"id\":\"a$n\"}"; code=201 ;;
  "GET /1/boards/b1") body='{"id":"b1","panels":[{"query_panel":{"query_annotation_id":"old1"}},{"query_panel":{"query_annotation_id":"old2","dataset":"software-factory"}}]}'; code=200 ;;
  "PUT /1/boards/b1") body='{"id":"b1","links":{"board_url":"https://ui.example/b1"}}'; code=200 ;;
  "POST /1/boards") body='{"id":"b-new","links":{"board_url":"https://ui.example/b-new"}}'; code=201 ;;
  "DELETE /1/query_annotations/"*) body=''; code=204 ;;
  *) body='{"error":"unexpected"}'; code=500 ;;
esac
printf '%s\n%s' "$body" "$code"
"""

FACTORY_COLS = ["name", "run_id", "agent", "subject", "phase", "status", "stop_reason", "error", "workflow_run_url",
                "model_calls", "reserved_calls", "throttles", "duration_s", "queue_s", "dry_run"]
HARNESS_COLS = ["name", "factory.agent", "factory.phase", "factory.model_id", "gen_ai.usage.input_tokens",
                "gen_ai.usage.output_tokens", "factory.model.from", "factory.model.to", "factory.model.reason",
                "factory.retry.reason", "rule", "guard"]


def _cols(names):
    return json.dumps([{"key_name": n} for n in names])


def _stub_run(tmp_path, board_id, agent_d_code=200, cols_f=FACTORY_COLS, cols_s=HARNESS_COLS, cols_s_code=200):
    d = tmp_path / "hc"
    shutil.copytree(BOARD_DIR, d)
    board = json.loads((d / "board.json").read_text())
    board["id"] = board_id
    (d / "board.json").write_text(json.dumps(board))
    cols = {c["alias"]: c for c in json.loads((d / "derived_columns.json").read_text())}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB)
    curl.chmod(0o755)
    log = tmp_path / "log"
    log.write_text("")
    e = _env(PATH=f"{bin_dir}:{os.environ['PATH']}", HONEYCOMB_CONFIG_KEY="k", HONEYCOMB_BOARD_DIR=str(d),
             STUB_LOG=str(log), STUB_COLS_F=_cols(cols_f), STUB_COLS_S=_cols(cols_s) if cols_s_code == 200 else "{}",
             STUB_COLS_S_CODE=str(cols_s_code), STUB_AGENT_D=json.dumps({"id": "dc1", **cols["agent_d"]}),
             STUB_AGENT_D_CODE=str(agent_d_code), STUB_CALLS=json.dumps({"id": "dc2", **cols["calls_ratio"], "description": "stale"}))
    r = subprocess.run([str(SCRIPT)], env=e, capture_output=True, text=True)
    calls = [line.split(" ", 2) for line in log.read_text().splitlines()]
    return r, calls, d


def test_update_path(tmp_path):
    r, calls, d = _stub_run(tmp_path, "b1")
    assert r.returncode == 0, r.stderr
    seq = [(c[0], c[1]) for c in calls]
    assert ("PUT", "/1/derived_columns/factory/dc2") in seq  # description differs
    assert ("PUT", "/1/derived_columns/factory/dc1") not in seq  # unchanged
    assert ("POST", "/1/boards") not in seq
    assert len([1 for m, p in seq if m == "POST" and p.startswith("/1/queries/")]) == len(PANELS)
    assert len([1 for m, p in seq if m == "POST" and p.startswith("/1/query_annotations/")]) == len(PANELS)
    assert seq.index(("GET", "/1/boards/b1")) < seq.index(("PUT", "/1/boards/b1"))
    deletes = [p for m, p in seq if m == "DELETE"]
    assert sorted(deletes) == ["/1/query_annotations/factory/old1", "/1/query_annotations/software-factory/old2"]
    assert seq.index(("PUT", "/1/boards/b1")) < seq.index(("DELETE", "/1/query_annotations/factory/old1"))
    put = json.loads(next(c[2] for c in calls if c[0] == "PUT" and c[1] == "/1/boards/b1"))
    assert put["id"] == "b1" and len(put["panels"]) == len(PANELS)
    assert "dataset" not in put["panels"][0]["query_panel"]
    assert "https://ui.example/b1" in r.stdout
    assert json.loads((d / "board.json").read_text())["id"] == "b1"


def test_missing_alias_creates_column_and_board_id_written(tmp_path):
    r, calls, d = _stub_run(tmp_path, None, agent_d_code=404)
    assert r.returncode == 0, r.stderr
    created = [json.loads(c[2]) for c in calls if (c[0], c[1]) == ("POST", "/1/derived_columns/factory")]
    assert [c["alias"] for c in created] == ["agent_d"]
    assert ("POST", "/1/boards") in [(c[0], c[1]) for c in calls]
    assert json.loads((d / "board.json").read_text())["id"] == "b-new"
    # The checked-in board.json is untouched.
    assert json.loads((BOARD_DIR / "board.json").read_text())["id"] is None


def test_failed_request_names_it(tmp_path):
    r, calls, _ = _stub_run(tmp_path, "unknown-board")
    assert r.returncode != 0
    assert "GET /1/boards/unknown-board" in r.stderr


def _panel_posts(calls):
    return [c for c in calls if c[0] == "POST" and c[1].startswith("/1/query_annotations/")]


def test_missing_column_skips_derived_column_and_panels(tmp_path):
    r, calls, d = _stub_run(tmp_path, None, agent_d_code=404,
                                cols_f=[c for c in FACTORY_COLS if c != "reserved_calls"])
    assert r.returncode == 0, r.stderr
    assert "skipping derived column calls_ratio" in r.stderr and "reserved_calls" in r.stderr
    assert "skipping panel 05-model-calls-vs-cap" in r.stderr
    created = [json.loads(c[2])["alias"] for c in calls if (c[0], c[1]) == ("POST", "/1/derived_columns/factory")]
    assert created == ["agent_d"]
    assert len(_panel_posts(calls)) == len(PANELS) - 1
    assert ("POST", "/1/boards") in [(c[0], c[1]) for c in calls]
    assert f"applied {len(PANELS) - 1} of {len(PANELS)} panels" in r.stderr


def test_missing_harness_dataset_skips_harness_panels(tmp_path):
    r, calls, _ = _stub_run(tmp_path, None, cols_s_code=404)
    assert r.returncode == 0, r.stderr
    harness = [p for p in PANELS if json.loads(p.read_text())["dataset"] == "software-factory"]
    assert harness
    for p in harness:
        assert f"skipping panel {p.stem}" in r.stderr
    assert not [c for c in calls if c[1].startswith("/1/queries/software-factory")]
    assert len(_panel_posts(calls)) == len(PANELS) - len(harness)
    assert ("POST", "/1/boards") in [(c[0], c[1]) for c in calls]


def test_no_applicable_panel_leaves_board_alone(tmp_path):
    r, calls, d = _stub_run(tmp_path, "b1", cols_f=[], cols_s=[])
    assert r.returncode == 0, r.stderr
    assert f"applied 0 of {len(PANELS)} panels" in r.stderr
    assert not [c for c in calls if "/boards" in c[1]]
    assert json.loads((d / "board.json").read_text())["id"] == "b1"
