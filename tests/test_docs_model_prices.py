"""Guards that the README documents where model costs come from."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_has_a_model_price_section():
    assert "## 模型价格" in read_readme()


def test_documents_the_fallback_chain():
    r = read_readme()
    assert "LiteLLM" in r
    assert "OpenRouter" in r
    assert "models.dev" in r


def test_documents_the_unit():
    assert "USD / 百万 Token" in read_readme()


def test_documents_exact_matching():
    r = read_readme()
    assert "不做前缀" in r or "不做通配" in r


def test_admin_panel_list_mentions_prices():
    r = read_readme()
    assert "- **模型价格**" in r


def test_price_section_comes_before_the_api_section():
    r = read_readme()
    assert r.index("## 模型价格") < r.index("## API")
