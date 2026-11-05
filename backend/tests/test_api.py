"""API tests against the test database: run lifecycle, lineage, TCA, exports rights, roles,
and the OpenAPI contract (diffed against the committed schema)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import WINDOW

OPENAPI = Path(__file__).parent / "golden" / "openapi.json"


@pytest.fixture(scope="module")
def client(db):  # type: ignore[no-untyped-def]
    from simp.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def run_id(client):  # type: ignore[no-untyped-def]
    from simp.runs.orchestrator import execute

    cfg = {"window": WINDOW.model_dump(mode="json"), "initial_aum": 1e9, "name": "api test"}
    r = client.post("/api/runs", json=cfg, headers={"x-simp-user": "tanish"})
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    # inline queue starts a thread; execute synchronously as well is unsafe, so wait for DONE
    import time

    for _ in range(600):
        s = client.get(f"/api/runs/{rid}").json()
        if s["status"] in ("DONE", "FAILED"):
            break
        time.sleep(0.5)
    assert s["status"] == "DONE", s.get("error")
    assert execute  # imported for side-effect-free reference
    return rid


def test_openapi_contract(client) -> None:  # type: ignore[no-untyped-def]
    live = client.get("/openapi.json").json()
    if not OPENAPI.exists():
        pytest.skip("no committed schema yet; run scripts/update_openapi.py")
    committed = json.loads(OPENAPI.read_text())
    assert sorted(live["paths"]) == sorted(committed["paths"]), (
        "API paths changed: regenerate the TypeScript client"
    )
    assert live["components"]["schemas"].keys() == committed["components"]["schemas"].keys()


def test_viewer_cannot_submit(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/runs", json={}, headers={"x-simp-user": "andrew"})
    assert r.status_code == 403


def test_run_audit_and_summary(client, run_id) -> None:  # type: ignore[no-untyped-def]
    r = client.get(f"/api/runs/{run_id}").json()
    s = r["summary"]
    assert s["audit"]["passed"] and s["audit"]["lineage_complete"]
    assert s["data_label"] == "SYNTHETIC"
    assert s["mapping"]["traded_notional_mapped_share"] >= 0.995
    assert r["output_hash"] and r["data_snapshot_hashes"]["lake_snapshot"]


def test_lineage_and_orders(client, run_id) -> None:  # type: ignore[no-untyped-def]
    page = client.get(f"/api/runs/{run_id}/orders", params={"level": "pm", "limit": 5}).json()
    assert page["total"] > 0
    pm = page["rows"][0]["pm_order_id"]
    lin = client.get(f"/api/orders/{pm}/lineage").json()
    assert lin["trader_orders"] and lin["fills"] and lin["timeline"][0]["kind"] == "decision"
    for lvl in ("trader", "child", "fill"):
        assert client.get(f"/api/runs/{run_id}/orders", params={"level": lvl, "limit": 3}).status_code == 200


def test_tca_endpoints(client, run_id) -> None:  # type: ignore[no-untyped-def]
    t = client.get(f"/api/runs/{run_id}/tca", params={"group_by": "size_bucket"}).json()
    assert t["weighting"] == "notional" and t["groups"]
    for m in ("T0", "MOC", "VWAP", "ARRIVAL", "HANDOFF"):
        assert (
            client.get(f"/api/runs/{run_id}/tca", params={"method": m, "metric": "vs_close"}).status_code
            == 200
        )
    assert client.get(f"/api/runs/{run_id}/tca/heatmap").json()["cells"]
    assert client.get(f"/api/runs/{run_id}/tca/methods").json()["methods"]


def test_exports_refuse_raw_and_watermark(client, run_id) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/exports", json={"run_id": run_id, "table": "market_daily"})
    assert r.status_code == 403
    r = client.post("/api/exports", json={"run_id": run_id, "table": "tca_order"})
    assert r.status_code == 200
    assert r.text.startswith("# SIMP export") and "SYNTHETIC" in r.text.splitlines()[0]


def test_merger_cannot_be_approved(client) -> None:  # type: ignore[no-untyped-def]
    ca = client.get("/api/data/corporate-actions", params={"type": "MG"}).json()
    if not ca:
        pytest.skip("no merger in the small world")
    x = ca[0]
    r = client.post(
        f"/api/data/corporate-actions/{x['permno']}/{x['ex_date']}/MG", json={"status": "APPROVED"}
    )
    assert r.status_code == 422
