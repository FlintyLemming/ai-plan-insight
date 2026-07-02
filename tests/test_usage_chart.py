from ai_plan_insight.config import Config


def test_alias_lookup_reverses_alias_arrays():
    cfg = Config(
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


def test_alias_lookup_empty_when_no_aliases():
    assert Config(providers={}).alias_lookup == {}


def test_alias_lookup_duplicate_raw_id_last_definition_wins():
    # Same raw id "x" appears in two arrays; the later key wins.
    cfg = Config(
        providers={},
        model_aliases={"A": ["x"], "B": ["x"]},
    )
    assert cfg.alias_lookup["x"] == "B"
