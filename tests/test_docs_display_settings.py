"""Guards that the README documents the push-card display TTL setting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_admin_panel_list_mentions_display_settings():
    assert "- **显示设置**" in read_readme()
