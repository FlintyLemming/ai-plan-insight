# ai_plan_insight/admin_api.py
"""/api/admin/* routes.

Two routers: `router` holds the two endpoints the login screen itself needs
(`/session`, `/login`); `protected` holds everything else behind
`require_admin`. web.py mounts both.
"""
from __future__ import annotations

import logging
from contextlib import closing as _closing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import admin_auth, config_store, provider_types, usage_store
from .instance_config import _INSTANCE_ID_RE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")
protected = APIRouter(
    prefix="/api/admin", dependencies=[Depends(admin_auth.require_admin)]
)


def closing_conn():
    """`with closing_conn() as conn:` — a fresh usage-DB connection."""
    return _closing(admin_auth.open_conn())


MASK = "••••"


def mask_secret(value: str) -> str:
    """`sk-1234ab12` -> `sk-••••ab12`; anything shorter than 8 is fully masked."""
    if not value:
        return ""
    if len(value) < 8:
        return MASK
    return f"{value[:3]}{MASK}{value[-4:]}"


def _secret_field_names(type_name: str) -> set[str]:
    t = provider_types.get_type(type_name)
    return {f.name for f in t.fields if f.secret} if t is not None else set()


def _instance_to_json(row: config_store.InstanceRow) -> dict:
    secret_names = _secret_field_names(row.type)
    return {
        "instance_id": row.instance_id,
        "type": row.type,
        "mode": row.mode,
        "label": row.label,
        "order": row.order,
        "enabled": row.enabled,
        "credentials": {
            name: (mask_secret(value) if name in secret_names else value)
            for name, value in row.credentials.items()
        },
    }


class LoginRequest(BaseModel):
    password: str


class PasswordChangeRequest(BaseModel):
    old: str
    new: str


class InstancePayload(BaseModel):
    type: str
    mode: str
    label: str
    order: int = 999
    enabled: bool = True
    # None means "keep the stored value", "" means "clear it".
    credentials: dict[str, str | None] = Field(default_factory=dict)


class InstanceCreateRequest(InstancePayload):
    instance_id: str


# ── public ────────────────────────────────────────────────────

@router.get("/session")
async def get_session(request: Request):
    with closing_conn() as conn:
        configured = admin_auth.password_configured(conn)
        authenticated = configured and admin_auth.validate_session(
            conn, request.cookies.get(admin_auth.COOKIE_NAME)
        )
    return {"authenticated": authenticated, "password_configured": configured}


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if admin_auth.rate_limiter.is_locked(ip):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    with closing_conn() as conn:
        stored = config_store.get_setting(conn, "admin_password_hash")
        if not stored:
            raise HTTPException(status_code=503, detail="管理密码未设置")
        if not admin_auth.verify_password(req.password, stored):
            admin_auth.rate_limiter.record_failure(ip)
            raise HTTPException(status_code=401, detail="密码错误")
        admin_auth.rate_limiter.record_success(ip)
        token = admin_auth.create_session(conn)
    admin_auth.set_session_cookie(response, request, token)
    return {"ok": True}


# ── session management ────────────────────────────────────────

@protected.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(admin_auth.COOKIE_NAME)
    with closing_conn() as conn:
        if token:
            admin_auth.delete_session(conn, token)
    admin_auth.clear_session_cookie(response)
    return {"ok": True}


@protected.post("/logout-all")
async def logout_all(response: Response):
    with closing_conn() as conn:
        admin_auth.delete_all_sessions(conn)
    admin_auth.clear_session_cookie(response)
    return {"ok": True}


@protected.post("/password")
async def change_password(req: PasswordChangeRequest, request: Request, response: Response):
    if len(req.new) < admin_auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"新密码至少 {admin_auth.MIN_PASSWORD_LENGTH} 位",
        )
    with closing_conn() as conn:
        stored = config_store.get_setting(conn, "admin_password_hash")
        if not admin_auth.verify_password(req.old, stored):
            raise HTTPException(status_code=403, detail="原密码错误")
        config_store.set_setting(conn, "admin_password_hash", admin_auth.hash_password(req.new))
        # Every other session dies; this client gets a fresh one.
        admin_auth.delete_all_sessions(conn)
        token = admin_auth.create_session(conn)
    admin_auth.set_session_cookie(response, request, token)
    return {"ok": True}


# ── provider types ────────────────────────────────────────────

@protected.get("/provider-types")
async def get_provider_types():
    return provider_types.to_json()


# ── instances ─────────────────────────────────────────────────

@protected.get("/instances")
async def list_instances():
    with closing_conn() as conn:
        rows = config_store.list_instances(conn, include_disabled=True)
    errors: dict[str, str] = {}
    for row in rows:
        try:
            config_store.validate_row(row)
        except Exception as e:
            errors[row.instance_id] = str(e)
    return {"instances": [_instance_to_json(r) for r in rows], "errors": errors}


@protected.get("/instances/{instance_id}/secrets")
async def get_instance_secrets(instance_id: str):
    with closing_conn() as conn:
        row = config_store.get_instance(conn, instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"实例 {instance_id} 不存在")
    return {"credentials": row.credentials}


@protected.post("/instances", status_code=201)
async def create_instance(req: InstanceCreateRequest):
    if not _INSTANCE_ID_RE.fullmatch(req.instance_id):
        raise HTTPException(status_code=422, detail="实例 ID 需匹配 [A-Za-z0-9._-]+")
    credentials = {k: v for k, v in req.credentials.items() if v}
    row = config_store.InstanceRow(
        instance_id=req.instance_id, type=req.type, mode=req.mode,
        label=req.label, order=req.order, enabled=req.enabled,
        credentials=credentials,
    )
    with closing_conn() as conn:
        try:
            config_store.create_instance(conn, row)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@protected.put("/instances/{instance_id}")
async def update_instance(instance_id: str, req: InstancePayload):
    with closing_conn() as conn:
        existing = config_store.get_instance(conn, instance_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"实例 {instance_id} 不存在")

        credentials = dict(existing.credentials)
        for name, value in req.credentials.items():
            if value is None:
                continue  # keep whatever is stored
            if value == "":
                credentials.pop(name, None)
            else:
                credentials[name] = value
        # Switching type drops credentials the new type does not accept.
        allowed = provider_types.allowed_credential_fields().get(req.type, set())
        credentials = {k: v for k, v in credentials.items() if k in allowed}

        row = config_store.InstanceRow(
            instance_id=instance_id, type=req.type, mode=req.mode,
            label=req.label, order=req.order, enabled=req.enabled,
            credentials=credentials,
        )
        try:
            config_store.update_instance(conn, instance_id, row)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"实例 {instance_id} 不存在")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@protected.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str):
    with closing_conn() as conn:
        try:
            config_store.delete_instance(conn, instance_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"实例 {instance_id} 不存在")
    return {"ok": True}


# ── push sources (moved here from web.py; now behind login) ───

@protected.get("/sources")
async def get_sources():
    """Authentication status of every source that has reported.

    It exposes which push sources are authenticating successfully, so it
    belongs behind the login even though nothing in the dashboard calls it.
    """
    try:
        with closing_conn() as conn:
            return usage_store.get_source_auth_status(conn)
    except Exception as e:
        logger.error("admin sources failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
