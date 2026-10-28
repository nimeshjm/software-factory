"""Honeycomb Query Data API wrapper (read-only)."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from strands import tool

from factory.config import load_config

from .profile import get_profile

MAX_ROWS = 50


def _client() -> httpx.Client:
    """Overridable in tests (monkeypatch to inject a MockTransport)."""
    return httpx.Client(timeout=30.0)


def run_query(query_spec: dict[str, Any], *, api_base: str, dataset: str, environment: str | None = None,
              key: str, sleep=time.sleep) -> dict[str, Any]:
    base = api_base.rstrip("/")
    headers = {"X-Honeycomb-Team": key, "Content-Type": "application/json"}
    with _client() as c:
        r = c.post(f"{base}/1/queries/{dataset}", headers=headers, json=query_spec)
        r.raise_for_status()
        qid = r.json()["id"]
        r = c.post(f"{base}/1/query_results/{dataset}", headers=headers, json={"query_id": qid, "disable_series": True})
        r.raise_for_status()
        rid = r.json()["id"]
        for _ in range(30):
            r = c.get(f"{base}/1/query_results/{dataset}/{rid}", headers=headers)
            r.raise_for_status()
            body = r.json()
            if body.get("complete"):
                return body
            sleep(1.0)
    raise TimeoutError("honeycomb query did not complete in time")


def compact(body: dict[str, Any], max_rows: int = MAX_ROWS) -> dict[str, Any]:
    rows = [row.get("data", row) for row in (body.get("data") or {}).get("results", [])]
    return {"row_count": len(rows), "rows": rows[:max_rows], "truncated": len(rows) > max_rows}


@tool
def honeycomb_query(query_json: str) -> str:
    """Run a Honeycomb query (Query Data API) against the target's configured dataset and return compact rows.

    Args:
        query_json: JSON query spec, e.g. {"calculations":[{"op":"COUNT"}],"filters":[{"column":"error","op":"=","value":true}],"breakdowns":["error.type"],"time_range":3600,"limit":50}.
    """
    key = os.environ.get("HONEYCOMB_QUERY_KEY")
    if not key:
        return "error: HONEYCOMB_QUERY_KEY is not set"
    try:
        spec = json.loads(query_json)
        if not isinstance(spec, dict):
            raise ValueError("query must be a JSON object")
    except ValueError as e:
        return f"error: invalid query_json: {e}"
    spec.setdefault("time_range", 3600)
    spec.setdefault("limit", MAX_ROWS)
    hc = get_profile().honeycomb
    if not hc.enabled and os.environ.get("FACTORY_HONEYCOMB_FORCE") != "1":
        return "error: honeycomb is disabled in the target profile (honeycomb.enabled=false)"
    if not hc.dataset:
        return "error: honeycomb.dataset is not set in the target profile"
    try:
        body = run_query(spec, api_base=load_config()["honeycomb"]["api_base"], dataset=hc.dataset,
                         environment=hc.environment, key=key)
    except Exception as e:  # noqa: BLE001 - tool must never raise into the loop
        return f"error: honeycomb query failed: {type(e).__name__}: {e}"[:500]
    return json.dumps(compact(body), separators=(",", ":"), default=str)[:20_000]
