"""Relational schema (Postgres 16). Foreign keys enforce the order lineage:
fill -> child_order -> trader_order -> pm_order -> decision -> run."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
J = JSON().with_variant(JSONB(), "postgresql")
TS = DateTime(timezone=True)
QTY = Numeric(20, 4)
PX = Numeric(20, 6)
USD = Numeric(24, 6)

users = Table(
    "users",
    metadata,
    Column("username", String(64), primary_key=True),
    Column("role", String(16), nullable=False),
    Column("display_name", String(128)),
    Column("created_at", TS),
    CheckConstraint("role in ('viewer','researcher','admin')", name="ck_users_role"),
)

fund = Table(
    "fund",
    metadata,
    Column("fund_id", String(64), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("strategy_type", String(16), nullable=False),
    Column("base_ccy", String(3), nullable=False, default="USD"),
    Column("benchmark_id", String(32), nullable=False),
    Column("inception_date", Date, nullable=False),
    Column("initial_aum", USD, nullable=False),
    Column("cash_buffer_bps", Float, nullable=False),
    Column("fee_bps", Float, nullable=False),
    Column("description", Text),
)

broker = Table(
    "broker",
    metadata,
    Column("broker_id", String(32), primary_key=True),
    Column("name", String(128), nullable=False),
    Column("commission_bps", Float, nullable=False),
    Column("portfolio_fee_bps", Float),
    Column("specialty", String(32)),
)

desk = Table("desk", metadata, Column("desk_id", String(32), primary_key=True), Column("name", String(128)))

venue = Table(
    "venue",
    metadata,
    Column("mic", String(8), primary_key=True),
    Column("name", String(128)),
    Column("kind", String(16)),
    Column("supports_auction", Boolean),
    Column("fee_remove", Float),
    Column("fee_add", Float),
    Column("fee_auction", Float),
    Column("fee_midpoint", Float),
)

desk_policy = Table(
    "desk_policy",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("yaml", Text, nullable=False),
    Column("updated_by", String(64)),
    Column("updated_at", TS),
)

run = Table(
    "run",
    metadata,
    Column("run_id", String(26), primary_key=True),
    Column("fund_id", String(64), ForeignKey("fund.fund_id"), nullable=False),
    Column("name", String(256)),
    Column("config", J, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("code_version", String(64)),
    Column("data_snapshot_hashes", J),
    Column("data_label", String(32)),
    Column("status", String(16), nullable=False),
    Column("stage", String(32)),
    Column("progress", Float),
    Column("submitted_by", String(64)),
    Column("submitted_at", TS),
    Column("started_at", TS),
    Column("finished_at", TS),
    Column("error", J),
    Column("summary", J),
    Column("output_hash", String(64)),
    Column("study_id", String(26)),
    Column("study_step", Integer),
    Index("ix_run_fund", "fund_id"),
)

run_event = Table(
    "run_event",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("ts", TS, nullable=False),
    Column("kind", String(16), nullable=False),  # stage | progress | log | error | done
    Column("payload", J),
    Index("ix_run_event_run", "run_id", "id"),
)

study = Table(
    "study",
    metadata,
    Column("study_id", String(26), primary_key=True),
    Column("kind", String(32), nullable=False),  # counterfactual_chain | sensitivity
    Column("name", String(256)),
    Column("base_config", J),
    Column("steps", J),
    Column("created_by", String(64)),
    Column("created_at", TS),
)

decision = Table(
    "decision",
    metadata,
    Column("decision_id", String(26), primary_key=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("fund_id", String(64), nullable=False),
    Column("decision_type", String(32), nullable=False),
    Column("snapshot_date", Date, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("decision_ts", TS, nullable=False),
    Column("target_weights_ref", String(26)),
    Index("ix_decision_run", "run_id"),
)

target_weight = Table(
    "target_weight",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column(
        "decision_id", String(26), ForeignKey("decision.decision_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("permno", Integer, primary_key=True),
    Column("weight", Float, nullable=False),
    Column("weight_source", String(32)),
    Column("excluded_flag", Boolean, nullable=False),
    Column("exclusion_reason", Text),
    Index("ix_tw_decision", "decision_id"),
)

pm_order = Table(
    "pm_order",
    metadata,
    Column("pm_order_id", String(26), primary_key=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("fund_id", String(64), nullable=False),
    Column("decision_id", String(26), ForeignKey("decision.decision_id", ondelete="CASCADE"), nullable=False),
    Column("permno", Integer, nullable=False),
    Column("listing_id", BigInteger),
    Column("side", String(4), nullable=False),
    Column("quantity", QTY, nullable=False),
    Column("reason", String(24), nullable=False),
    Column("decision_ts", TS, nullable=False),
    Column("decision_price", PX, nullable=False),
    Column("decision_price_source", String(16), nullable=False),
    Column("instruction", String(16), nullable=False),
    Column("deadline_ts", TS, nullable=False),
    Column("urgency", String(8), nullable=False),
    Column("limit_price", PX),
    Column("status", String(12), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("exec_date", Date, nullable=False),
    Column("quantity_adjustment_factor", Float, nullable=False),
    Column("filled_qty", QTY, nullable=False),
    Column("answer_key", J),
    Column("flags", J),
    CheckConstraint("quantity > 0", name="ck_pm_qty_pos"),
    CheckConstraint("side in ('BUY','SELL')", name="ck_pm_side"),
    CheckConstraint("decision_ts <= deadline_ts", name="ck_pm_ts"),
    Index("ix_pm_run_date", "run_id", "exec_date"),
    Index("ix_pm_run_permno", "run_id", "permno"),
    Index("ix_pm_decision", "decision_id"),
)

program = Table(
    "program",
    metadata,
    Column("program_id", String(26), primary_key=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("broker_id", String(32), ForeignKey("broker.broker_id"), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("n_orders", Integer, nullable=False),
    Column("notional", USD, nullable=False),
    Column("fee_bps", Float, nullable=False),
    Index("ix_program_run", "run_id"),
)

trader_order = Table(
    "trader_order",
    metadata,
    Column("trader_order_id", String(26), primary_key=True),
    Column("pm_order_id", String(26), ForeignKey("pm_order.pm_order_id", ondelete="CASCADE"), nullable=False),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("desk_id", String(32), ForeignKey("desk.desk_id"), nullable=False),
    Column("strategy", String(16), nullable=False),
    Column("program_id", String(26), ForeignKey("program.program_id", ondelete="CASCADE")),
    Column("broker_id", String(32), ForeignKey("broker.broker_id"), nullable=False),
    Column("quantity", QTY, nullable=False),
    Column("filled_qty", QTY, nullable=False),
    Column("start_ts", TS, nullable=False),
    Column("end_ts", TS, nullable=False),
    Column("arrival_price", PX, nullable=False),
    Column("params", J),
    Column("status", String(10), nullable=False),
    Column("commission_bps", Float, nullable=False),
    CheckConstraint("quantity > 0", name="ck_to_qty_pos"),
    CheckConstraint("start_ts <= end_ts", name="ck_to_window"),
    Index("ix_to_pm", "pm_order_id"),
    Index("ix_to_run", "run_id"),
    Index("ix_to_program", "program_id"),
)

child_order = Table(
    "child_order",
    metadata,
    Column("child_order_id", String(26), primary_key=True),
    Column(
        "trader_order_id",
        String(26),
        ForeignKey("trader_order.trader_order_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("broker_id", String(32), ForeignKey("broker.broker_id"), nullable=False),
    Column("venue_mic", String(8), ForeignKey("venue.mic"), nullable=False),
    Column("order_type", String(14), nullable=False),
    Column("quantity", QTY, nullable=False),
    Column("limit_price", PX),
    Column("sent_ts", TS, nullable=False),
    Column("tif", String(9), nullable=False),
    Column("status", String(10), nullable=False),
    Index("ix_child_to", "trader_order_id"),
    Index("ix_child_run", "run_id"),
)

fill = Table(
    "fill",
    metadata,
    Column("fill_id", String(26), primary_key=True),
    Column(
        "child_order_id",
        String(26),
        ForeignKey("child_order.child_order_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "trader_order_id",
        String(26),
        ForeignKey("trader_order.trader_order_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("pm_order_id", String(26), ForeignKey("pm_order.pm_order_id", ondelete="CASCADE"), nullable=False),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("broker_id", String(32), ForeignKey("broker.broker_id"), nullable=False),
    Column("venue_mic", String(8), ForeignKey("venue.mic"), nullable=False),
    Column("exec_ts", TS, nullable=False),
    Column("price", PX, nullable=False),
    Column("quantity", QTY, nullable=False),
    Column("liquidity", String(8), nullable=False),
    Column("market_state", String(20), nullable=False),
    Column("fees_usd", USD, nullable=False),
    Column("commission_usd", USD, nullable=False),
    Column("fill_model", String(32), nullable=False),
    Column("ref_snapshot", J),
    Column("permno", Integer, nullable=False),
    Column("side", String(4), nullable=False),
    CheckConstraint("quantity > 0", name="ck_fill_qty_pos"),
    Index("ix_fill_to", "trader_order_id"),
    Index("ix_fill_run", "run_id"),
    Index("ix_fill_child", "child_order_id"),
    Index("ix_fill_pm", "pm_order_id"),
)

ledger_entry = Table(
    "ledger_entry",
    metadata,
    Column("entry_id", String(26), primary_key=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("fund_id", String(64), nullable=False),
    Column("ts", TS, nullable=False),
    Column("account", String(12), nullable=False),
    Column("permno", Integer),
    Column("amount", USD, nullable=False),
    Column("quantity", QTY, nullable=False),
    Column("ref_type", String(16), nullable=False),
    Column("ref_id", String(64)),
    Index("ix_ledger_run", "run_id", "ts"),
)

flow = Table(
    "flow",
    metadata,
    Column("flow_id", String(26), primary_key=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("fund_id", String(64), nullable=False),
    Column("date", Date, nullable=False),
    Column("amount_usd", USD, nullable=False),
    Column("units", Numeric(24, 8), nullable=False),
    Column("unit_nav_used", Numeric(20, 8), nullable=False),
    Column("generator_seed", BigInteger),
    Column("is_jump", Boolean),
    Index("ix_flow_run", "run_id", "date"),
)

nav_daily = Table(
    "nav_daily",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("fund_id", String(64), nullable=False),
    Column("cash", USD),
    Column("equity_value", USD),
    Column("receivables", USD),
    Column("payables", USD),
    Column("nav", USD, nullable=False),
    Column("units", Numeric(24, 8)),
    Column("unit_nav", Numeric(20, 10)),
    Column("fund_return", Float),
    Column("gross_return", Float),
    Column("flow_usd", USD),
    Column("bench_return_tr", Float),
    Column("bench_return_pr", Float),
    Column("bench_return_official", Float),
    Column("cash_weight", Float),
    Column("fees_usd", USD),
    Column("trading_cost_usd", USD),
)

position_daily = Table(
    "position_daily",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("permno", Integer, primary_key=True),
    Column("shares", QTY, nullable=False),
    Column("close_price", PX),
    Column("market_value", USD),
    Column("split_factor_applied", Float),
)

benchmark_daily = Table(
    "benchmark_daily",
    metadata,
    Column("benchmark_id", String(32), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("return_tr", Float),
    Column("return_pr", Float),
    Column("source", String(64)),
    Column("snapshot", String(64)),
)

corporate_action = Table(
    "corporate_action",
    metadata,
    Column("permno", Integer, primary_key=True),
    Column("ex_date", Date, primary_key=True),
    Column("type", String(4), primary_key=True),
    Column("pay_date", Date),
    Column("declared_date", Date),
    Column("amount", Float),
    Column("factor", Float),
    Column("review_status", String(12), nullable=False),
    Column("reviewed_by", String(64)),
    Column("note", Text),
    Column("source_ids", J),
)

mapping_review = Table(
    "mapping_review",
    metadata,
    Column("permno", Integer, primary_key=True),
    Column("valid_from", Date, primary_key=True),
    Column("ticker", String(16)),
    Column("isin", String(12)),
    Column("valid_to", Date),
    Column("reason", Text),
    Column("status", String(12), nullable=False),  # OPEN | FIXED | EXCLUDED
    Column("fixed_listing_id", BigInteger),
    Column("reviewed_by", String(64)),
    Column("snapshot", String(64)),
)

data_job = Table(
    "data_job",
    metadata,
    Column("job_id", String(26), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("source", String(16), nullable=False),
    Column("status", String(12), nullable=False),
    Column("args", J),
    Column("result", J),
    Column("error", Text),
    Column("created_by", String(64)),
    Column("created_at", TS),
    Column("finished_at", TS),
)

netting = Table(
    "netting",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("permno", Integer, primary_key=True),
    Column("crossed_shares", QTY),
    Column("saved_notional", USD),
    Column("reasons", J),
)

tca_order = Table(
    "tca_order",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column(
        "pm_order_id", String(26), ForeignKey("pm_order.pm_order_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("date", Date, nullable=False),
    Column("permno", Integer, nullable=False),
    Column("ticker", String(16)),
    Column("side", String(4)),
    Column("reason", String(24)),
    Column("instruction", String(16)),
    Column("main_strategy", String(16)),
    Column("main_broker", String(32)),
    Column("main_venue", String(8)),
    Column("quantity", QTY),
    Column("filled_qty", QTY),
    Column("notional", USD),
    Column("exec_notional", USD),
    Column("decision_price", PX),
    Column("arrival_price", PX),
    Column("avg_price", PX),
    Column("close_price", PX),
    Column("vwap_day", PX),
    Column("vwap_interval", PX),
    Column("is_bps", Float),
    Column("delay_bps", Float),
    Column("execution_bps", Float),
    Column("explicit_bps", Float),
    Column("opportunity_bps", Float),
    Column("exec_cost_bps", Float),
    Column("slip_close_bps", Float),
    Column("slip_vwap_bps", Float),
    Column("slip_arrival_bps", Float),
    Column("slip_benchmark_bps", Float),
    Column("pct_adv", Float),
    Column("pct_auction", Float),
    Column("pct_exp_auction", Float),
    Column("spread_bps", Float),
    Column("vol_intraday", Float),
    Column("imbalance_side", String(4)),
    Column("imbalance_notional", USD),
    Column("size_bucket", String(12)),
    Column("auction_bucket", String(12)),
    Column("auction_share", Float),
    Column("passive_share", Float),
    Column("markout_1s", Float),
    Column("markout_5s", Float),
    Column("markout_60s", Float),
    Column("markout_300s", Float),
    Column("next_open_vs_close_bps", Float),
    Column("fees_usd", USD),
    Column("commission_usd", USD),
    Column("cost_usd", USD),
    Column("flags", J),
    Column("answer_key", J),
    Index("ix_tca_run_date", "run_id", "date"),
    Index("ix_tca_pm", "pm_order_id"),
)

reprice = Table(
    "reprice",
    metadata,
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), primary_key=True),
    Column(
        "pm_order_id", String(26), ForeignKey("pm_order.pm_order_id", ondelete="CASCADE"), primary_key=True
    ),
    Column("date", Date, nullable=False),
    Column("t0_price", PX),
    Column("t0_bps", Float),
    Column("moc_price", PX),
    Column("moc_bps", Float),
    Column("moc_vs_close_bps", Float),
    Column("vwap_price", PX),
    Column("vwap_bps", Float),
    Column("arrival_price", PX),
    Column("arrival_bps", Float),
    Column("handoff_price", PX),
    Column("handoff_bps", Float),
    Column("notional", USD),
    Index("ix_reprice_run_date", "run_id", "date"),
    Index("ix_reprice_pm", "pm_order_id"),
)

ca_event = Table(
    "ca_event",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String(26), ForeignKey("run.run_id", ondelete="CASCADE"), nullable=False),
    Column("date", Date, nullable=False),
    Column("permno", Integer, nullable=False),
    Column("kind", String(4), nullable=False),
    Column("shares", QTY),
    Column("amount", USD),
    Column("factor", Float),
    Column("booked", Boolean),
    Column("provisional", Boolean),
    Index("ix_ca_event_run", "run_id"),
)

export = Table(
    "export",
    metadata,
    Column("export_id", String(26), primary_key=True),
    Column("run_id", String(26)),
    Column("table_name", String(64)),
    Column("format", String(8)),
    Column("rows", Integer),
    Column("watermark", Text),
    Column("created_by", String(64)),
    Column("created_at", TS),
)
