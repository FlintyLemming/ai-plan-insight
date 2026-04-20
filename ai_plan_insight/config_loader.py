import json
from pathlib import Path

from .config import Config, ProviderConfig

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def load_config(path: str | None = None) -> Config:
    """Load configuration from a JSON file."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = json.load(f)

    return Config.model_validate(raw)
