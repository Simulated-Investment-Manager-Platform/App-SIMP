"""Dramatiq workers (Redis 7): one queue per job type — runs, data, reports.

Start with:  uv run dramatiq simp.runs.worker --queues runs data
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from simp.logging import configure_logging
from simp.settings import get_settings

configure_logging()
broker = RedisBroker(url=get_settings().redis_url)
dramatiq.set_broker(broker)


@dramatiq.actor(queue_name="runs", max_retries=0, time_limit=4 * 3600 * 1000)
def run_actor(run_id: str) -> None:
    from simp.runs.orchestrator import execute

    execute(run_id)


@dramatiq.actor(queue_name="data", max_retries=0, time_limit=4 * 3600 * 1000)
def data_actor(job_id: str) -> None:
    from simp.data.jobs import execute_job

    execute_job(job_id)
