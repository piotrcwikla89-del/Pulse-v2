"""Ustawienia planisty przechowywane w system_settings."""

from __future__ import annotations

PLANNER_SETTING_KEYS = {
    "planner_pdf_root": "",
    "planner_scan_interval_min": "5",
    "planner_watcher_enabled": "0",
    "planner_last_scan_at": "",
    "planner_last_scan_summary": "",
}


def get_planner_setting(cur, key: str, default: str | None = None) -> str:
    cur.execute("SELECT value FROM system_settings WHERE key=?", (key,))
    row = cur.fetchone()
    if row is None:
        if default is not None:
            return default
        return PLANNER_SETTING_KEYS.get(key, "")
    return row["value"] if hasattr(row, "keys") else row[0]


def set_planner_setting(cur, key: str, value: str) -> None:
    cur.execute("SELECT 1 FROM system_settings WHERE key=?", (key,))
    if cur.fetchone():
        cur.execute("UPDATE system_settings SET value=? WHERE key=?", (value, key))
    else:
        cur.execute("INSERT INTO system_settings (key, value) VALUES (?, ?)", (key, value))


def ensure_planner_settings(cur, execute) -> None:
    for key, default in PLANNER_SETTING_KEYS.items():
        execute(cur, "SELECT 1 FROM system_settings WHERE key=?", (key,))
        if not cur.fetchone():
            execute(cur, "INSERT INTO system_settings (key, value) VALUES (?, ?)", (key, default))


def get_all_planner_settings(cur) -> dict[str, str]:
    return {key: get_planner_setting(cur, key) for key in PLANNER_SETTING_KEYS}
