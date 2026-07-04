#!/usr/bin/env python3
"""Seed fake usage data for testing mobile UI."""
import sqlite3
import sys
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC8 = timezone(timedelta(hours=8))

# Fake model names to test with (mix of short and long names)
FAKE_MODELS = [
    "gpt-4o-2024-11-20",
    "gpt-4o-mini",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-haiku-latest",
    "claude-sonnet-4-20250514",
    "gemini-2.5-pro-preview-06-05",
    "gemini-2.5-flash-preview-05-20",
    "glm-4.6",
    "glm-4.5-air",
    "doubao-seed-2.0-pro",
    "doubao-seed-2.0-lite",
    "kimi-k2-20250711",
    "deepseek-reasoner",
    "deepseek-v3.1",
    "qwen3.5-max-2025-07",
    "minimax-m2.5",
    "step-2-16k",
]

FAKE_SOURCES = [
    ("cursor", "Cursor"),
    ("claude_code", "Claude Code"),
    ("codex", "Codex CLI"),
    ("cline", "Cline"),
    ("aider", "Aider"),
    ("mimo", "Mimo"),
]


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS usage_point (
        date                TEXT NOT NULL,
        source_id           TEXT NOT NULL,
        model_id            TEXT NOT NULL,
        input_tokens        INTEGER NOT NULL,
        output_tokens       INTEGER NOT NULL,
        cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
        updated_at          TEXT NOT NULL,
        PRIMARY KEY (date, source_id, model_id)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS source (
        source_id     TEXT PRIMARY KEY,
        label         TEXT,
        last_seen     TEXT,
        frozen_before TEXT
    )
    """)
    conn.commit()


def seed(db_path: Path, days: int = 90) -> None:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_schema(conn)

    today = datetime.now(UTC8).date()
    now_iso = datetime.now(UTC8).isoformat()
    random.seed(42)  # reproducible

    total_points = 0
    for day_offset in range(days - 1, -1, -1):
        date = (today - timedelta(days=day_offset)).isoformat()
        # Weekend dip
        date_obj = today - timedelta(days=day_offset)
        is_weekend = date_obj.weekday() >= 5
        # Number of active models varies per day
        n_active = random.randint(6, len(FAKE_MODELS))
        active_models = random.sample(FAKE_MODELS, n_active)
        active_sources = random.sample(FAKE_SOURCES, random.randint(2, len(FAKE_SOURCES)))

        for src_id, src_label in active_sources:
            # Mark 7+ days ago as frozen
            frozen = day_offset >= 7
            frozen_before = (today - timedelta(days=6)).isoformat() if frozen else None
            conn.execute(
                "INSERT OR REPLACE INTO source (source_id, label, last_seen, frozen_before) VALUES (?, ?, ?, ?)",
                (src_id, src_label, now_iso, frozen_before),
            )
            for model in active_models:
                # Base usage varies by model popularity
                base = random.choice([500_000, 2_000_000, 8_000_000, 20_000_000, 50_000_000])
                scale = 0.3 if is_weekend else 1.0
                inp = int(random.gauss(base * scale, base * scale * 0.4))
                out = int(random.gauss(base * 0.4 * scale, base * 0.4 * scale * 0.4))
                cr = int(inp * random.uniform(0.1, 0.6)) if random.random() > 0.2 else 0
                cw = int(inp * random.uniform(0.05, 0.2)) if random.random() > 0.3 else 0
                rs = int(out * random.uniform(0.5, 2.5)) if random.random() > 0.4 else 0
                inp = max(0, inp)
                out = max(0, out)
                cr = max(0, cr)
                cw = max(0, cw)
                rs = max(0, rs)
                if inp + out == 0:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO usage_point "
                    "(date, source_id, model_id, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (date, src_id, model, inp, out, cr, cw, rs, now_iso),
                )
                total_points += 1

    conn.commit()
    conn.close()
    print(f"Seeded {total_points} data points across {days} days into {db_path}")


if __name__ == "__main__":
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "test_data" / "usage.db"
    seed(out_path)
