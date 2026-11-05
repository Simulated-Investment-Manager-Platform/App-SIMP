"""Copy the handoff package CSVs into tests/golden/handoff/ for the golden-replay test.

Usage: uv run python scripts/import_handoff.py /path/to/BMLL_TCA_Supervisor_Handoff/
"""

import shutil
import sys
from pathlib import Path

WANTED = [
    "portfolio_nav.csv",
    "orderbook.csv",
    "orderbook_tca_handoff_split_normalized.csv",
    "quarterly_coverage_cleaned.csv",
]

src = Path(sys.argv[1])
dst = Path(__file__).resolve().parents[1] / "tests" / "golden" / "handoff"
dst.mkdir(parents=True, exist_ok=True)
for name in WANTED:
    hits = list(src.rglob(name))
    if hits:
        shutil.copy2(hits[0], dst / name)
        print(f"copied {hits[0]} -> {dst / name}")
    else:
        print(f"missing {name}")
