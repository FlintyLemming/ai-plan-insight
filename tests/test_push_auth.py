from ai_plan_insight.config import Config


def test_config_push_auth_defaults():
    cfg = Config(providers={})
    assert cfg.push_auth_secret == ""
    assert cfg.enforce_push_auth is False


def test_config_push_auth_parses_secret_and_enforce():
    cfg = Config(providers={}, push_auth_secret="abc", enforce_push_auth=True)
    assert cfg.push_auth_secret == "abc"
    assert cfg.enforce_push_auth is True
