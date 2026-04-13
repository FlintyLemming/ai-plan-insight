from datetime import datetime
from .models import UsageInfo, LimitDetail

MONEY_BALANCE_KEYS = {"余额", "总余额", "赠送余额", "充值余额", "balance", "total", "gift", "recharge"}


def _format_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _compute_percentage(used: str, limit: str) -> int | None:
    try:
        used_val = int(used)
        limit_val = int(limit)
        if limit_val == 0:
            return None
        return int(used_val * 100 / limit_val)
    except (ValueError, TypeError):
        return None


def _format_time_window(limit: LimitDetail) -> str:
    if limit.limit_type == "TIME_LIMIT":
        return "MCP 调用数量"
    if limit.time_unit == "TOKENS_LIMIT":
        return "模型调用"
    if limit.time_unit in ("hour", "minute", "day", "second", "week", "month", "year"):
        return f"{limit.duration} {limit.time_unit}"
    unit = limit.time_unit.replace("TIME_UNIT_", "").lower()
    return f"{limit.duration} {unit}"


def format_usage_simple(usages: list[UsageInfo]) -> str:
    lines = []
    for usage in usages:
        lines.append(f"\n{'=' * 60}")
        lines.append(f"Provider: {usage.provider}")
        if usage.user_id:
            lines.append(f"User ID: {usage.user_id}")
        if usage.membership_level:
            lines.append(f"Membership: {usage.membership_level}")

        if usage.limits:
            lines.append("\n  Rate Limits:")
            for limit in usage.limits:
                time_window = _format_time_window(limit)
                pct = _compute_percentage(limit.used, limit.limit)
                pct_str = f" ({pct}%)" if pct is not None else ""

                lines.append(
                    f"    - {time_window}: {limit.used}/{limit.limit}{pct_str}"
                )
                if limit.reset_time:
                    lines.append(f"      Reset: {_format_datetime(limit.reset_time)}")
                if limit.usage_details:
                    lines.append("      Usage by model:")
                    for detail in limit.usage_details:
                        lines.append(f"        - {detail.model_code}: {detail.usage}")
        elif not usage.balances:
            lines.append("\n  No rate limits available.")

        if usage.balances:
            lines.append("\n  Balances:")
            for key, value in usage.balances.items():
                display_value = f"¥{value}" if key in MONEY_BALANCE_KEYS else value
                lines.append(f"    - {key}: {display_value}")

        if usage.token_usage:
            period_labels = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days"}
            lines.append("\n  Token Usage:")
            for tu in usage.token_usage:
                label = period_labels.get(tu.period, tu.period)
                lines.append(f"    - {label}: {tu.total_tokens:,} tokens ({tu.total_calls:,} calls)")

    lines.append(f"\n{'=' * 60}")
    return "\n".join(lines)
