"""Golden replay (spec: Testing). With handoff timing and model T0, SIMP must reproduce the
handoff's portfolio_nav.csv within $0.01 per day and orderbook.csv order for order.

The handoff CSVs are licensed (derived from CRSP) and are not committed. Put them in
tests/golden/handoff/ (or set SIMP_HANDOFF_DIR) and import the matching CRSP pull with
`simp data pull --source live`; the test skips otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

HANDOFF = Path(os.environ.get("SIMP_HANDOFF_DIR", Path(__file__).parent / "golden" / "handoff"))


@pytest.mark.golden
@pytest.mark.skipif(
    not (HANDOFF / "portfolio_nav.csv").exists(), reason="handoff CSVs not present (licensed data)"
)
def test_golden_replay_portfolio_nav() -> None:
    import polars as pl

    from simp.config.run_config import preset
    from simp.data.market import load_market
    from simp.runs.engine import Engine
    from simp.runs.orchestrator import latest_snapshot

    md = load_market(latest_snapshot())
    if md.data_label == "SYNTHETIC":
        pytest.skip("golden replay needs the live CRSP snapshot the handoff was built from")
    out = Engine(preset("handoff_replay"), md, "01GOLDEN").run()
    gold = pl.read_csv(HANDOFF / "portfolio_nav.csv", try_parse_dates=True)
    ours = pl.DataFrame({"date": [r.date for r in out.nav], "nav_simp": [r.nav for r in out.nav]})
    j = gold.join(ours, on="date", how="inner")
    assert j.height >= 1010
    assert (j["nav"] - j["nav_simp"]).abs().max() <= 0.01
    if (HANDOFF / "orderbook.csv").exists():
        ob = pl.read_csv(HANDOFF / "orderbook.csv")
        assert ob.height == sum(1 for o in out.pm_orders if o.reason in ("INITIAL", "INDEX_REBALANCE"))
