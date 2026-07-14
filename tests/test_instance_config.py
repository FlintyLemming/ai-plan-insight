# tests/test_instance_config.py
import json
import pytest
from pathlib import Path
from ai_plan_insight.instance_config import (
    V2InstanceConfig,
    V2Config,
    load_v2_config,
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
                    "type": "claude",
                    "mode": "push",
                    "label": "个人号",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        cfg = load_v2_config(str(p))
        assert "claude-personal" in cfg.providers

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_v2_config(str(tmp_path / "nope.json"))

    def test_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{bad json}")
        with pytest.raises(Exception):
            load_v2_config(str(p))

    def test_invalid_instance_id(self, tmp_path: Path):
        data = {
            "providers": {
                "has space": {
                    "type": "claude",
                    "mode": "push",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="instance_id"):
            load_v2_config(str(p))

    def test_unknown_type(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "nonexistent_provider",
                    "mode": "push",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="type"):
            load_v2_config(str(p))

    def test_unknown_mode(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "invalid",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="mode"):
            load_v2_config(str(p))

    def test_unsupported_type_mode_combo(self, tmp_path: Path):
        # claude only supports push, not fetch
        data = {
            "providers": {
                "claude-fetch": {
                    "type": "claude",
                    "mode": "fetch",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="type.*mode"):
            load_v2_config(str(p))

    def test_empty_label_rejected(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "push",
                    "label": "   ",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="label"):
            load_v2_config(str(p))

    def test_unknown_field_rejected(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "push",
                    "label": "test",
                    "totally_bogus_field": True,
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_v2_config(str(p))


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
