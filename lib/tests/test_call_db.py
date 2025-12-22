import os
import tempfile
from datetime import date, timedelta

from call.lib import call_db


def test_update_and_read_cost_totals(tmp_path, monkeypatch):
    db_path = tmp_path / "cost.db"
    monkeypatch.setenv("CALL_DB", str(db_path))
    # Ensure module uses new path
    import importlib

    importlib.reload(call_db)

    res1 = call_db.update_cost_totals(1.0, "USD")
    assert res1 is not None
    assert res1.total_cost == 1.0
    assert res1.total_cost_today == 1.0
    assert res1.last_updated_date == date.today().isoformat()
    assert res1.currency == "USD"

    # Force last date to yesterday to test daily reset
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        "UPDATE media_gen_blender_bot_cost_totals SET last_updated_date=? WHERE id=1",
        (yesterday,),
    )
    conn.commit()
    conn.close()

    res2 = call_db.update_cost_totals(2.0, "USD")
    assert res2 is not None
    assert res2.total_cost == 3.0  # cumulative
    assert res2.total_cost_today == 2.0  # daily reset then add
    assert res2.last_updated_date == date.today().isoformat()
    assert res2.currency == "USD"

    # Read back
    read = call_db.read_cost_totals()
    assert read == res2
