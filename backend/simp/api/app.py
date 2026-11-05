"""FastAPI service (v1). Typed responses; the OpenAPI schema generates the TypeScript client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from simp.api.routers import admin, market, orders, runs, tca
from simp.errors import SimpError
from simp.logging import configure_logging
from simp.settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    from simp.db.seed import seed_reference

    seed_reference()
    yield


app = FastAPI(
    title="SIMP API",
    version="1.0.0",
    lifespan=lifespan,
    description="Simulated Investment Manager Platform: funds, runs, order chain, TCA, market data.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SimpError)
async def simp_error(_: Request, e: SimpError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(e),
            "type": type(e).__name__,
            "session": str(e.session) if e.session else None,
        },
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


for r in (admin.router, runs.router, orders.router, tca.router, market.router):
    app.include_router(r, prefix="/api")
