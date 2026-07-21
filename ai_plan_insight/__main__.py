import argparse
import logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display remaining usage for AI coding plan subscriptions (web only)."
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to configuration file (default: config.json in the project root).",
    )
    parser.add_argument(
        "--usage-db", default=None,
        help="Path to the usage SQLite DB (default: <config dir>/usage.db, or ./data/usage.db).",
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Run as a web server (default and only mode).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind the web server to (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Port to bind the web server to (default: 8765).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.web:
        print("ai-plan-insight is web-only. Pass --web to start the server.", flush=True)
        return

    import uvicorn
    import ai_plan_insight.web as web_mod

    web_mod._config_path = args.config
    if args.usage_db:
        from pathlib import Path
        web_mod._usage_db_path = Path(args.usage_db)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    uvicorn.run(web_mod.app, host=args.host, port=args.port, log_level="info")


def cli() -> None:
    main()


if __name__ == "__main__":
    main()
