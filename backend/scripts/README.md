# scripts/

One line each, as the handoff review asked.

- `update_openapi.py`: writes `tests/golden/openapi.json`. The API contract test diffs against it, and CI fails if the API changes without regenerating it (then run `pnpm gen:api` in `frontend/`).
- `import_handoff.py`: copies the handoff CSVs (`portfolio_nav.csv`, `orderbook*.csv`, `quarterly_coverage_cleaned.csv`) into `tests/golden/handoff/` for the golden-replay test. They are licensed and never committed.
