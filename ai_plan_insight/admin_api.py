# ai_plan_insight/admin_api.py
"""/api/admin/* routes.

Two routers: `router` holds the two endpoints the login screen itself needs
(`/session`, `/login`); `protected` holds everything else behind
`require_admin`. web.py mounts both.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import closing as _closing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import admin_auth, config_store, provider_types, usage_store
from .instance_config import V2InstanceConfig, _INSTANCE_ID_RE, _validate_instance
from .provider_registry import build_fetch_provider, get_push_request_model

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


class AliasesRequest(BaseModel):
    # `model_aliases` collides with Pydantic's protected `model_` namespace.
    model_config = {"protected_namespaces": ()}

    model_aliases: dict[str, list[str]]


class PushAuthRequest(BaseModel):
    push_auth_secret: str
    enforce_push_auth: bool


class TestConnectionRequest(BaseModel):
    type: str
    mode: str
    instance_id: str = ""
    credentials: dict[str, str] = Field(default_factory=dict)


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


# ── model aliases ─────────────────────────────────────────────

@protected.get("/aliases")
async def get_aliases():
    with closing_conn() as conn:
        return {"model_aliases": config_store.get_aliases(conn)}


@protected.put("/aliases")
async def put_aliases(req: AliasesRequest):
    with closing_conn() as conn:
        try:
            config_store.replace_aliases(conn, req.model_aliases)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


# ── push auth ─────────────────────────────────────────────────

@protected.get("/push-auth")
async def get_push_auth():
    with closing_conn() as conn:
        secret, enforce = config_store.get_push_auth(conn)
    return {"push_auth_secret": mask_secret(secret), "enforce_push_auth": enforce}


@protected.get("/push-auth/secret")
async def get_push_auth_secret():
    with closing_conn() as conn:
        secret, _ = config_store.get_push_auth(conn)
    return {"push_auth_secret": secret}


@protected.put("/push-auth")
async def put_push_auth(req: PushAuthRequest):
    with closing_conn() as conn:
        config_store.set_push_auth(conn, req.push_auth_secret, req.enforce_push_auth)
    return {"ok": True}


# ── export / import ───────────────────────────────────────────

@protected.get("/export")
async def export_config():
    """Full config as JSON, including plaintext credentials. The admin
    password hash is never part of it."""
    with closing_conn() as conn:
        return config_store.export_config(conn)


@protected.post("/import")
async def import_config(payload: dict):
    with closing_conn() as conn:
        try:
            config_store.import_config(conn, payload)
        except config_store.ImportValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"message": "导入配置校验失败，未写入任何内容", "errors": e.errors},
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


# ── connection test ───────────────────────────────────────────

TEST_TIMEOUT_SECONDS = 15


async def _run_fetch_test(cfg: V2InstanceConfig):
    """Build a throwaway provider and run one full fetch cycle.

    Nothing is written to the DB and nothing enters the runtime cache;
    monkeypatched wholesale in tests.
    """
    provider = build_fetch_provider("connection-test", cfg)
    provider.authenticate()
    raw = await provider.fetch_usage()
    return provider.parse_usage(raw)


@protected.post("/instances/test")
async def test_instance(req: TestConnectionRequest):
    t = provider_types.get_type(req.type)
    if t is None:
        raise HTTPException(status_code=422, detail=f"未知类型 {req.type}")
    if req.mode not in t.modes:
        raise HTTPException(
            status_code=422, detail=f"类型 {req.type} 不支持 {req.mode} 模式"
        )

    if req.mode == "push":
        with closing_conn() as conn:
            secret, _ = config_store.get_push_auth(conn)
        model = get_push_request_model(req.type)
        instance_id = req.instance_id or "{instance_id}"
        return {
            "ok": True,
            "push": {
                "url": f"/api/push/v2/{instance_id}",
                "push_auth_configured": bool(secret),
                "payload_schema": model.model_json_schema() if model else None,
            },
        }

    try:
        cfg = V2InstanceConfig(
            type=req.type, mode="fetch", label="连接测试", **req.credentials
        )
        _validate_instance("connection-test", cfg)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        parsed = await asyncio.wait_for(_run_fetch_test(cfg), timeout=TEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"测试超时（{TEST_TIMEOUT_SECONDS} 秒）"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "summary": {
            "membership_level": parsed.membership_level,
            "limits": [
                f"{lim.duration} {lim.time_unit}：{lim.used}/{lim.limit}"
                for lim in parsed.limits
            ],
            "balances": dict(parsed.balances),
        },
    }
