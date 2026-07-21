import subprocess
import sys
from pathlib import Path


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "ai_plan_insight", *args],
        capture_output=True, text=True, timeout=20,
    )


def test_no_web_flag_prints_hint_and_exits_zero():
    r = _run("--config", "config.json")
    assert r.returncode == 0
    assert "--web" in r.stdout or "--web" in r.stderr


def test_v2_config_flag_is_not_accepted():
    r = _run("--v2-config", "x.json")
    assert r.returncode != 0  # argparse rejects unknown flag
    assert "unrecognized" in r.stderr or "unrecognized arguments" in r.stderr
