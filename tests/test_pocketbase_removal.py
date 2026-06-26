from pathlib import Path

import pytest

from ai_plan_insight.config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "ai_plan_insight/pocketbase_store.py",
        "pb_collections_glm.json",
        ".env.example",
    ],
)
def test_pocketbase_artifacts_are_removed(relative_path: str):
    assert not (ROOT / relative_path).exists()


def test_web_no_longer_references_pocketbase_runtime():
    source = (ROOT / "ai_plan_insight" / "web.py").read_text(encoding="utf-8")

    assert "pocketbase_store" not in source
    assert "background_store_glm" not in source
    assert "task_pb" not in source


def test_config_ignores_legacy_pocketbase_key():
    config = Config.model_validate(
        {
            "providers": {},
            "pocketbase": {
                "url": "https://pb.example.test",
                "email": "admin@example.test",
                "password": "secret",
            },
        }
    )

    assert config.providers == {}
    assert not hasattr(config, "pocketbase")
