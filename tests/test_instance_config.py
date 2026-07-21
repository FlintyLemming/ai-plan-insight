# tests/test_instance_config.py
import json
import pytest
from pathlib import Path
from ai_plan_insight.instance_config import (
    V2InstanceConfig,
    V2Config,
    load_v2_config,
    LoadResult,
    resolve_v2_config_path,
    _INSTANCE_ID_RE,
)


class TestInstanceIdRegex:
    def test_valid_ids(self):
        for vid in ["claude-personal", "bigmodel.work", "my_instance", "abc123", "a-b.c_d"]:
            assert _INSTANCE_ID_RE.fullmatch(vid)

    def test_invalid_ids(self):
        for vid in ["", "has space", "slash/bad", "colon:bad", "中文", "a@b"]:
            assert not _INSTANCE_ID_RE.fullmatch(vid)


class TestV2InstanceConfig:
    def test_minimal_push(self):
        cfg = V2InstanceConfig(type="claude", mode="push", label="个人号")
        assert cfg.type == "claude"
        assert cfg.mode == "push"
        assert cfg.label == "个人号"
        assert cfg.order == 999

    def test_fetch_with_credentials(self):
        cfg = V2InstanceConfig(
            type="bigmodel", mode="fetch", label="工作号",
            api_key="sk-xxx", order=20,
        )
        assert cfg.api_key == "sk-xxx"
        assert cfg.order == 20

    def test_empty_label_rejected(self):
        with pytest.raises(Exception):
            V2InstanceConfig(type="claude", mode="push", label="   ")

    def test_invalid_mode_rejected(self):
        with pytest.raises(Exception):
            V2InstanceConfig(type="claude", mode="invalid", label="test")


class TestV2Config:
    def test_empty_providers(self):
        cfg = V2Config(providers={})
        assert cfg.providers == {}
        assert cfg.push_auth_secret == ""
        assert cfg.enforce_push_auth is False

    def test_full_config(self):
        raw = {
            "providers": {
                "claude-personal": {
                    "type": "claude",
                    "mode": "push",
                    "label": "个人号",
                    "order": 12,
                },
                "bigmodel-work": {
                    "type": "bigmodel",
                    "mode": "fetch",
                    "label": "工作账号",
                    "api_key": "sk-xxx",
                    "order": 20,
                },
            },
            "push_auth_secret": "secret123",
            "enforce_push_auth": True,
        }
        cfg = V2Config.model_validate(raw)
        assert len(cfg.providers) == 2
        assert cfg.providers["claude-personal"].type == "claude"
        assert cfg.providers["bigmodel-work"].api_key == "sk-xxx"
        assert cfg.push_auth_secret == "secret123"
        assert cfg.enforce_push_auth is True


class TestLoadV2Config:
    def test_load_valid(self, tmp_path: Path):
        data = {
            "providers": {
                "claude-personal": {
                    "type": "claude", "mode": "push", "label": "个人号",
                }
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert result.instance_errors == {}
        assert "claude-personal" in result.config.providers

    def test_file_not_found(self, tmp_path: Path):
        result = load_v2_config(str(tmp_path / "nope.json"))
        assert result.config_error is not None
        assert "not found" in result.config_error
        assert result.config.providers == {}

    def test_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{bad json}")
        result = load_v2_config(str(p))
        assert result.config_error is not None
        assert result.config.providers == {}

    def test_top_level_unknown_field_is_config_error(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"providers": {}, "bogus_top": 1}))
        result = load_v2_config(str(p))
        # V2Config extra="forbid" rejects unknown top-level fields
        assert result.config_error is not None
        assert result.config.providers == {}

    def test_unknown_type_skips_instance_only(self, tmp_path: Path):
        data = {
            "providers": {
                "bad-type": {
                    "type": "nonexistent_provider", "mode": "push", "label": "x",
                },
                "claude-personal": {
                    "type": "claude", "mode": "push", "label": "ok",
                },
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "claude-personal" in result.config.providers
        assert "bad-type" not in result.config.providers
        assert "bad-type" in result.instance_errors
        assert "unknown type" in result.instance_errors["bad-type"]

    def test_invalid_instance_id_skips_instance_only(self, tmp_path: Path):
        data = {
            "providers": {
                "has space": {"type": "claude", "mode": "push", "label": "x"},
                "ok": {"type": "claude", "mode": "push", "label": "y"},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "ok" in result.config.providers
        assert "has space" in result.instance_errors

    def test_unknown_mode_skips_instance_only(self, tmp_path: Path):
        data = {
            "providers": {
                "bad": {"type": "claude", "mode": "invalid", "label": "x"},
                "ok": {"type": "claude", "mode": "push", "label": "y"},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "ok" in result.config.providers
        assert "bad" in result.instance_errors

    def test_unsupported_type_mode_combo_skips_instance(self, tmp_path: Path):
        data = {
            "providers": {
                "claude-fetch": {"type": "claude", "mode": "fetch", "label": "x"},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "claude-fetch" not in result.config.providers
        assert "claude-fetch" in result.instance_errors

    def test_empty_label_skips_instance(self, tmp_path: Path):
        data = {
            "providers": {
                "bad": {"type": "claude", "mode": "push", "label": "   "},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "bad" in result.instance_errors

    def test_unknown_field_skips_instance(self, tmp_path: Path):
        data = {
            "providers": {
                "bad": {
                    "type": "claude", "mode": "push", "label": "x",
                    "totally_bogus_field": True,
                },
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "bad" in result.instance_errors

    def test_missing_required_credential_skips_instance(self, tmp_path: Path):
        data = {
            "providers": {
                "no-key": {"type": "bigmodel", "mode": "fetch", "label": "x"},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        result = load_v2_config(str(p))
        assert result.config_error is None
        assert "no-key" not in result.config.providers
        assert "no-key" in result.instance_errors


class TestResolveV2ConfigPath:
    def test_explicit_path(self, tmp_path: Path):
        p = tmp_path / "custom.json"
        assert resolve_v2_config_path(str(p)) == p

    def test_from_config_dir(self, tmp_path: Path):
        # When config_path points to a dir's config.json, v2 should be in the same dir
        config_p = tmp_path / "config.json"
        config_p.write_text("{}")
        result = resolve_v2_config_path(str(config_p), config_path=str(config_p))
        assert result == tmp_path / "config.v2.json"

    def test_default_path(self):
        from ai_plan_insight.config_loader import DEFAULT_CONFIG_PATH
        result = resolve_v2_config_path(None, config_path=None)
        assert result == DEFAULT_CONFIG_PATH.parent / "config.v2.json"


class TestV2ConfigAliases:
    def test_model_aliases_default_empty(self):
        cfg = V2Config(providers={})
        assert cfg.model_aliases == {}

    def test_alias_lookup_reverses_arrays(self):
        cfg = V2Config(
            providers={},
            model_aliases={
                "GLM 5.2": ["glm-5.2", "glm5.2"],
                "GPT 5.2": ["gpt-5.2"],
            },
        )
        assert cfg.alias_lookup == {
            "glm-5.2": "GLM 5.2",
            "glm5.2": "GLM 5.2",
            "gpt-5.2": "GPT 5.2",
        }

    def test_alias_lookup_empty_when_no_aliases(self):
        assert V2Config(providers={}).alias_lookup == {}

    def test_alias_lookup_duplicate_raw_id_last_wins(self):
        cfg = V2Config(providers={}, model_aliases={"A": ["x"], "B": ["x"]})
        assert cfg.alias_lookup["x"] == "B"

    def test_default_config_path_points_to_repo_root_config(self):
        from ai_plan_insight.instance_config import DEFAULT_CONFIG_PATH
        assert DEFAULT_CONFIG_PATH.name == "config.json"
        assert DEFAULT_CONFIG_PATH.parent.name != "ai_plan_insight"
