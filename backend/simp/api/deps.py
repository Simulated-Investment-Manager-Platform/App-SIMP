"""Auth (single-team allow-list, roles viewer/researcher/admin) and DB helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import Engine, select, text

from simp.db import schema as S
from simp.db.session import get_engine
from simp.settings import get_settings

COOKIE = "simp_session"
RANK = {"viewer": 0, "researcher": 1, "admin": 2}


@dataclass
class User:
    username: str
    role: str

    def can(self, role: str) -> bool:
        return RANK.get(self.role, -1) >= RANK[role]


def serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret.get_secret_value(), salt="simp-session")


def db() -> Engine:
    return get_engine()


def _lookup(name: str) -> User | None:
    with get_engine().connect() as c:
        r = c.execute(select(S.users.c.username, S.users.c.role).where(S.users.c.username == name)).first()
    return User(r[0], r[1]) if r else None


def current_user(request: Request) -> User:
    s = get_settings()
    tok = request.cookies.get(COOKIE)
    name: str | None = None
    if tok:
        try:
            name = str(serializer().loads(tok)["u"])
        except BadSignature:
            name = None
    if name is None and s.auth_mode == "dev":
        name = request.headers.get("x-simp-user") or "tanish"
    if name is None:
        raise HTTPException(401, "sign in required")
    u = _lookup(name)
    if u is None:
        raise HTTPException(403, f"{name} is not on the SIMP allow-list")
    return u


def require(role: str) -> Any:
    def dep(u: User = Depends(current_user)) -> User:
        if not u.can(role):
            raise HTTPException(403, f"requires role {role}; you are {u.role}")
        return u

    return dep


def _py(v: Any) -> Any:
    if isinstance(v, Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with get_engine().connect() as c:
        return [{k: _py(v) for k, v in r.items()} for r in c.execute(text(sql), params).mappings().all()]


def one(sql: str, **params: Any) -> dict[str, Any] | None:
    r = rows(sql, **params)
    return r[0] if r else None
