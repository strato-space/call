import os
import sqlite3
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from call.lib.paths import default_event_db_path


DB_PATH = os.getenv("CALL_DB", str(default_event_db_path()))
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostTotals:
    total_cost: float
    total_cost_today: float
    last_updated_date: str
    currency: Optional[str]


def _ensure_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _ensure_cost_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_gen_blender_bot_cost_totals (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_cost REAL DEFAULT 0,
            total_cost_today REAL DEFAULT 0,
            last_updated_date TEXT,
            currency TEXT
        )
        """
    )
    # Add currency column for legacy tables
    cols = [row[1] for row in conn.execute("PRAGMA table_info(media_gen_blender_bot_cost_totals)")]
    if "currency" not in cols:
        conn.execute(
            "ALTER TABLE media_gen_blender_bot_cost_totals ADD COLUMN currency TEXT"
        )


def _normalize_row(row: tuple | None) -> tuple[float, float, str | None, Optional[str]]:
    if not row:
        return 0.0, 0.0, None, None
    total_all = float(row[0]) if row[0] is not None else 0.0
    total_today = float(row[1]) if row[1] is not None else 0.0
    last_date = row[2] if row[2] is not None else None
    currency = row[3] if len(row) > 3 else None
    return total_all, total_today, last_date, currency


def update_cost_totals(cost: float, currency: Optional[str] = None) -> Optional[CostTotals]:
    """Update totals and return CostTotals."""
    if cost is None or cost <= 0:
        return None
    today = date.today().isoformat()
    try:
        _ensure_dir(DB_PATH)
        with sqlite3.connect(DB_PATH) as conn:
            _ensure_cost_table(conn)
            row = conn.execute(
                "SELECT total_cost, total_cost_today, last_updated_date, currency FROM media_gen_blender_bot_cost_totals WHERE id = 1"
            ).fetchone()
            total_all, total_today, last_date, cur_currency = _normalize_row(row)
            if last_date != today:
                total_today = 0.0
            total_all += cost
            total_today += cost
            final_currency = currency or cur_currency
            conn.execute(
                """
                INSERT INTO media_gen_blender_bot_cost_totals (id, total_cost, total_cost_today, last_updated_date, currency)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_cost = excluded.total_cost,
                    total_cost_today = excluded.total_cost_today,
                    last_updated_date = excluded.last_updated_date,
                    currency = COALESCE(excluded.currency, media_gen_blender_bot_cost_totals.currency)
                """,
                (total_all, total_today, today, final_currency),
            )
        return CostTotals(
            total_cost=total_all,
            total_cost_today=total_today,
            last_updated_date=today,
            currency=final_currency,
        )
    except Exception:
        log.warning("cost totals update failed", exc_info=True)
        return None


def read_cost_totals() -> Optional[CostTotals]:
    """Return CostTotals or None if absent."""
    try:
        if not Path(DB_PATH).exists():
            return None
        with sqlite3.connect(DB_PATH) as conn:
            _ensure_cost_table(conn)
            row = conn.execute(
                "SELECT total_cost, total_cost_today, last_updated_date, currency FROM media_gen_blender_bot_cost_totals WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        total_all, total_today, last_date, currency = _normalize_row(row)
        return CostTotals(
            total_cost=total_all,
            total_cost_today=total_today,
            last_updated_date=last_date or date.today().isoformat(),
            currency=currency,
        )
    except Exception:
        log.warning("cost totals read failed", exc_info=True)
        return None
