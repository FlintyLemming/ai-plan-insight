from ai_plan_insight.instance_config import V2Config


def test_alias_lookup_reverses_alias_arrays():
    cfg = V2Config(
        providers={},
        model_aliases={"GLM 5.2": ["glm-5.2", "glm5.2"], "GPT 5.2": ["gpt-5.2"]},
    )
    assert cfg.alias_lookup == {
        "glm-5.2": "GLM 5.2", "glm5.2": "GLM 5.2", "gpt-5.2": "GPT 5.2",
    }


def test_alias_lookup_empty_when_no_aliases():
    assert V2Config(providers={}).alias_lookup == {}


def test_alias_lookup_duplicate_raw_id_last_wins():
    cfg = V2Config(providers={}, model_aliases={"A": ["x"], "B": ["x"]})
    assert cfg.alias_lookup["x"] == "B"


def test_load_v2_config_passes_model_aliases_through(tmp_path):
    import json
    from ai_plan_insight.instance_config import load_v2_config
    p = tmp_path / "config.json"
    p.write_text('{"providers": {}, "model_aliases": {"GLM 5.2": ["glm-5.2"]}}')
    result = load_v2_config(str(p))
    assert result.config_error is None
    assert result.config.model_aliases == {"GLM 5.2": ["glm-5.2"]}
    assert result.config.alias_lookup["glm-5.2"] == "GLM 5.2"
