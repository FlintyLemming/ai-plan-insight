import argparse
import asyncio
import logging
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display remaining usage for AI coding plan subscriptions."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to configuration file (default: config.json in the project root)",
    )
    parser.add_argument(
        "--usage-db",
        default=None,
        help="Path to the usage SQLite DB (default: <config dir>/usage.db, or ./data/usage.db)",
    )
    parser.add_argument(
        "--v2-config",
        default=None,
        help="Path to v2 configuration file (default: <config dir>/config.v2.json)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run as a web server",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the web server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind the web server to (default: 8765)",
    )
    return parser.parse_args()


async def _run_cli(args: argparse.Namespace) -> None:
    from .config import ProviderConfig
    from .config_loader import load_config
    from .models import UsageInfo
    from .providers.base import BaseProvider
    from .providers.kimi import KimiProvider
    from .providers.bigmodel import BigModelProvider
    from .providers.bigmodel_international import BigModelInternationalProvider
    from .providers.aiping import AipingProvider
    from .providers.huawei_cloud import HuaweiCloudBssProvider
    from .providers.codex import CodexProvider, CodexSecurityProvider
    from .providers.volcengine_ark import VolcEngineArkProvider
    from .providers.antigravity import AntigravityProvider
    from .formatter import format_usage_simple

    async def fetch_provider_usage(
        provider_name: str, provider_config: ProviderConfig
    ) -> UsageInfo:
        provider: BaseProvider
        match provider_name:
            case "kimi":
                provider = KimiProvider(provider_config)
            case "bigmodel":
                provider = BigModelProvider(provider_config)
            case "bigmodel_international":
                provider = BigModelInternationalProvider(provider_config)
            case "aiping":
                provider = AipingProvider(provider_config)
            case "huawei_cloud":
                provider = HuaweiCloudBssProvider(provider_config)
            case "codex":
                provider = CodexProvider(provider_config)
            case "codex_sub2api":
                provider = CodexSecurityProvider(provider_config)
            case "antigravity":
                provider = AntigravityProvider(provider_config)
            case "volcengine_ark":
                provider = VolcEngineArkProvider(provider_config)
            case _:
                raise ValueError(f"Unknown provider: {provider_name}")

        provider.authenticate()
        raw_data = await provider.fetch_usage()
        parsed = provider.parse_usage(raw_data)

        if hasattr(provider, "fetch_token_usage"):
            parsed.token_usage = await provider.fetch_token_usage()

        return parsed

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(
            "\nPlease create a config file and pass it via --config",
            file=sys.stderr,
        )
        sys.exit(1)

    usages: list[UsageInfo] = []

    # Providers that arrive via /api/push/* have no fetch implementation; skip
    # them in the CLI so a config entry carrying only `order` doesn't crash.
    push_only = {"cursor", "claude", "mimo_token_plan", "grok"}

    for provider_name, provider_config in config.providers.items():
        if provider_name in push_only:
            continue
        try:
            print(f"Fetching usage for {provider_name}...", file=sys.stderr)
            usage = await fetch_provider_usage(provider_name, provider_config)
            usages.append(usage)
        except Exception as e:
            print(f"Error fetching {provider_name} usage: {e}", file=sys.stderr)

    if usages:
        print(format_usage_simple(usages))
    else:
        print("No usage data retrieved.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    args = _parse_args()

    if args.web:
        import uvicorn
        import ai_plan_insight.web as web_mod

        web_mod._config_path = args.config
        if args.usage_db:
            from pathlib import Path
            web_mod._usage_db_path = Path(args.usage_db)
        if args.v2_config:
            web_mod._v2_config_path = args.v2_config

        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        uvicorn.run(web_mod.app, host=args.host, port=args.port, log_level="info")
    else:
        asyncio.run(_run_cli(args))


def cli() -> None:
    main()


if __name__ == "__main__":
    main()
