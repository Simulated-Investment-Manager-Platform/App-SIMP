"""Write the committed OpenAPI schema used by the API contract test and the TS client generator."""

import json
from pathlib import Path

from simp.api.app import app

out = Path(__file__).resolve().parents[1] / "tests" / "golden" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=1, sort_keys=True))
print(f"wrote {out}")
