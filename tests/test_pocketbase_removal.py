from pathlib import Path

import pytest


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


def test_config_rejects_legacy_pocketbase_key():
    from ai_plan_insight.instance_config import load_v2_config
    import json
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        p.write_text(json.dumps({
            "providers": {},
            "pocketbase": {"url": "https://pb.example.test", "email": "a@b.test", "password": "s"},
        }))
        result = load_v2_config(str(p))
        assert result.config_error is not None
        assert "pocketbase" in result.config_error or "unknown top-level" in result.config_error
        assert result.config.providers == {}
