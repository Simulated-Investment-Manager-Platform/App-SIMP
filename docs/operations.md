# Operations

- **Run lifecycle.** submit → data check (snapshot hashes re-verified) → mapping → simulate (SSE progress) → analyse (order-chain audit, TCA, fund report) → DONE or FAILED (class, message, failing session).
- **Reproducibility.** A run stores its config, `config_hash`, git SHA, lake snapshot hash, corporate-action review hash and desk-policy hash. `simp replay RUN_ID` re-runs and compares `output_hash`.
- **Rights.** Raw CRSP/BMLL rows stay in the lake. Exports are an allow-list of derived tables with a watermark naming the run, the config and the data label.
- **Logging.** Structured JSON logs (structlog) carry `run_id` and the session date on every line.
