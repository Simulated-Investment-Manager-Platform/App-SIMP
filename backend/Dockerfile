# SIMP backend image: API, worker and CLI share it (built from the uv lockfile).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project
COPY simp ./simp
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --all-extras
ENV PATH="/app/.venv/bin:$PATH" SIMP_LAKE_ROOT=/lake
EXPOSE 8000
CMD ["uvicorn", "simp.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
