# ai_plan_insight/provider_types.py
"""Provider type metadata — the single source of truth.

Drives three things at once: write-time validation (`instance_config`),
the admin form (`GET /api/admin/provider-types`), and card display names
(`provider_registry`). Adding a provider type means adding one entry here
plus its fetch factory / push request model + converter in
`provider_registry`; `tests/test_provider_types.py` goes red if the two
drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass

FETCH = frozenset({"fetch"})
PUSH = frozenset({"push"})
BOTH = frozenset({"fetch", "push"})


@dataclass(frozen=True)
class CredentialField:
    """One credential input on the admin form.

    `name` must be a field of `V2InstanceConfig`. `required_in` lists the
    modes in which the field must be non-empty; it must be a subset of the
    owning type's `modes`.
    """
    name: str
    label: str
    required_in: frozenset[str] = frozenset()
    secret: bool = False
    help: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class ProviderType:
    name: str
    display_name: str
    modes: frozenset[str]
    summary: str = ""
    doc_url: str = ""
    fields: tuple[CredentialField, ...] = ()


def _api_key(label: str = "API Key", help: str = "", placeholder: str = "sk-...") -> CredentialField:
    return CredentialField(
        name="api_key", label=label, required_in=FETCH,
        secret=True, help=help, placeholder=placeholder,
    )


PROVIDER_TYPES: dict[str, ProviderType] = {
    "kimi": ProviderType(
        name="kimi",
        display_name="Kimi Coding Plan",
        modes=FETCH,
        summary="拉取 Kimi Coding Plan 的订阅额度。",
        fields=(_api_key(help="Kimi 开放平台的 API Key。"),),
    ),
    "bigmodel": ProviderType(
        name="bigmodel",
        display_name="GLM Coding Plan",
        modes=FETCH,
        summary="拉取智谱 GLM Coding Plan 的订阅额度，并支持历史用量视图。",
        fields=(_api_key(help="智谱开放平台的 API Key。"),),
    ),
    "bigmodel_international": ProviderType(
        name="bigmodel_international",
        display_name="GLM Coding Plan 国际版",
        modes=FETCH,
        summary="拉取 GLM Coding Plan 国际版的订阅额度。",
        fields=(_api_key(help="GLM 国际版平台的 API Key。"),),
    ),
    "aiping": ProviderType(
        name="aiping",
        display_name="AIPing",
        modes=FETCH,
        summary="拉取 AIPing 的订阅额度。",
        fields=(_api_key(help="AIPing 的 API Key。"),),
    ),
    "huawei_cloud": ProviderType(
        name="huawei_cloud",
        display_name="华为云余额",
        modes=FETCH,
        summary="拉取华为云账号的现金余额。",
        fields=(
            CredentialField(
                name="user_name", label="账号名",
                required_in=FETCH,
                help="华为云账号名（不是 IAM 用户名）。",
            ),
            CredentialField(
                name="access_key_id", label="Access Key ID",
                required_in=FETCH,
                help="华为云 AK/SK 中的 AK。",
            ),
            CredentialField(
                name="access_key_secret", label="Secret Access Key",
                required_in=FETCH, secret=True,
                help="华为云 AK/SK 中的 SK。",
            ),
        ),
    ),
    "zenmux": ProviderType(
        name="zenmux",
        display_name="ZenMux",
        modes=FETCH,
        summary="同时拉取 ZenMux 的订阅配额与 PAYG 余额。",
        fields=(_api_key(
            label="Management API Key",
            help="ZenMux 的 Management API Key，不是普通推理 Key。",
        ),),
    ),
    "codex": ProviderType(
        name="codex",
        display_name="自购 Codex 中转站",
        modes=FETCH,
        summary="拉取 Sub2API 架构的自购 Codex 中转站额度。",
        fields=(
            _api_key(help="中转站签发的 API Key。"),
            CredentialField(
                name="base_url", label="接口地址",
                help="中转站的接口根地址，留空则用该类型的默认地址。",
                placeholder="https://example.com",
            ),
        ),
    ),
    "sub2api": ProviderType(
        name="sub2api",
        display_name="Sub2API 中转",
        modes=FETCH,
        summary="拉取 Sub2API 中转站的套餐与余额。",
        fields=(
            _api_key(help="中转站签发的 API Key。"),
            CredentialField(
                name="base_url", label="接口地址",
                help="中转站的接口根地址，留空则用该类型的默认地址。",
                placeholder="https://example.com",
            ),
        ),
    ),
    "antigravity": ProviderType(
        name="antigravity",
        display_name="Antigravity",
        modes=BOTH,
        summary="fetch 模式经 Antigravity Manager 的 API 获取；也可用 push 模式由本地 Agent 推送。",
        doc_url="https://github.com/lbjlaq/Antigravity-Manager",
        fields=(
            _api_key(help="Antigravity Manager 的 API Key（仅 fetch 模式需要）。"),
            CredentialField(
                name="base_url", label="Manager 地址",
                help="Antigravity Manager 的访问地址。",
                placeholder="http://127.0.0.1:8080",
            ),
            CredentialField(
                name="admin_password", label="管理密码",
                secret=True,
                help="Antigravity Manager 的管理密码，未设置可留空。",
            ),
        ),
    ),
    "volcengine_ark": ProviderType(
        name="volcengine_ark",
        display_name="火山方舟 Coding Plan",
        modes=FETCH,
        summary="拉取火山方舟 Coding Plan 的订阅额度。",
        fields=(
            CredentialField(
                name="access_key_id", label="Access Key ID",
                required_in=FETCH,
                help="火山引擎账号的 AK。",
            ),
            CredentialField(
                name="access_key_secret", label="Secret Access Key",
                required_in=FETCH, secret=True,
                help="火山引擎账号的 SK。",
            ),
        ),
    ),
    "claude": ProviderType(
        name="claude",
        display_name="Claude 订阅",
        modes=PUSH,
        summary="由本地 Agent 抓取 Claude 订阅用量后推送到面板。",
        doc_url="https://github.com/FlintyLemming/claude-sub-agent",
    ),
    "grok": ProviderType(
        name="grok",
        display_name="Grok 订阅",
        modes=PUSH,
        summary="由本地 Agent 抓取 Grok 的月/周配额后推送到面板。",
    ),
    "cursor": ProviderType(
        name="cursor",
        display_name="Cursor",
        modes=PUSH,
        summary="由本地 Agent 抓取 Cursor 用量后推送到面板。",
        doc_url="https://github.com/FlintyLemming/cursor-usage-agent",
    ),
    "mimo_token_plan": ProviderType(
        name="mimo_token_plan",
        display_name="小米 MiMo Token Plan",
        modes=PUSH,
        summary="由本地 Agent 抓取 MiMo Token Plan 用量后推送到面板。",
        doc_url="https://github.com/FlintyLemming/mimo-usage-agent",
        fields=(
            CredentialField(
                name="cookie", label="Cookie",
                secret=True,
                help="可选，供 Agent 侧使用；面板本身不用它发请求。",
            ),
        ),
    ),
    "qianwen": ProviderType(
        name="qianwen",
        display_name="千问 Token Plan",
        modes=PUSH,
        summary="由本地 Agent 抓取千问 Token Plan 用量后推送到面板。",
    ),
}


def get_type(name: str) -> ProviderType | None:
    return PROVIDER_TYPES.get(name)


def supported_types() -> dict[str, set[str]]:
    """type -> allowed modes."""
    return {name: set(t.modes) for name, t in PROVIDER_TYPES.items()}


def allowed_credential_fields() -> dict[str, set[str]]:
    """type -> credential field names it may carry (may be empty)."""
    return {name: {f.name for f in t.fields} for name, t in PROVIDER_TYPES.items()}


def required_credential_fields() -> dict[tuple[str, str], set[str]]:
    """(type, mode) -> credential field names that must be non-empty.

    Combinations with no required field are omitted, matching the shape of
    the hand-written table this replaces.
    """
    out: dict[tuple[str, str], set[str]] = {}
    for name, t in PROVIDER_TYPES.items():
        for mode in t.modes:
            required = {f.name for f in t.fields if mode in f.required_in}
            if required:
                out[(name, mode)] = required
    return out


def display_name(name: str) -> str:
    t = PROVIDER_TYPES.get(name)
    return t.display_name if t is not None else name


def to_json() -> list[dict]:
    """Full metadata table for the admin form, sorted by display name."""
    return [
        {
            "name": t.name,
            "display_name": t.display_name,
            "modes": sorted(t.modes),
            "summary": t.summary,
            "doc_url": t.doc_url,
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "required_in": sorted(f.required_in),
                    "secret": f.secret,
                    "help": f.help,
                    "placeholder": f.placeholder,
                }
                for f in t.fields
            ],
        }
        for t in sorted(PROVIDER_TYPES.values(), key=lambda t: t.display_name)
    ]
