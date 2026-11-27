"""Dashboard magazynu farb — KPI jak w arkuszu Dashboard Excela + snapshot dzienny."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from typing import Any

from helpers import alert_daty
from time_utils import local_date_str, local_datetime_str, local_now, local_today


def _age_days(data_produkcji) -> int | None:
    if not data_produkcji or str(data_produkcji).strip() in ("0", "-", ""):
        return None
    from datetime import date, datetime

    try:
        if hasattr(data_produkcji, "date"):
            d = data_produkcji.date() if not isinstance(data_produkcji, date) else data_produkcji
        else:
            d = datetime.strptime(str(data_produkcji).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (local_today() - d).days


def _row_get(row, key, idx=None, default=None):
    if hasattr(row, "keys"):
        try:
            return row[key]
        except Exception:  # noqa: BLE001
            return default
    if idx is not None:
        try:
            return row[idx]
        except Exception:  # noqa: BLE001
            return default
    return default


def _count_ops(cur, typ: str, since_date: str, until_date: str | None = None) -> tuple[int, float]:
    """Zwraca (liczba, suma wag) operacji danego typu w oknie dat."""

    until = until_date or local_date_str(local_now() + timedelta(days=1))
    # data bywa pusta po imporcie — odrzucamy śmieci zanim CAST padnie na Postgresie.
    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(CAST(ilosc AS REAL)), 0) AS masa
        FROM operacje
        WHERE typ=?
          AND data IS NOT NULL AND TRIM(data) != ''
          AND date(data) >= ? AND date(data) < ?
        """,
        (typ, since_date, until),
    )
    row = cur.fetchone()
    n = int(_row_get(row, "n", 0, 0) or 0)
    masa = float(_row_get(row, "masa", 1, 0) or 0)
    return n, masa


def build_magazyn_dashboard(cur) -> dict[str, Any]:
    """Liczy aktualny stan + aktywność + zapełnienie półek."""

    from services.magazyn_import import ensure_magazyn_aux_tables

    ensure_magazyn_aux_tables(cur)

    cur.execute("SELECT id, pantone, polka, waga, status, data_produkcji FROM farby")
    farby = cur.fetchall()

    counts = {"dostepna": 0, "w_uzyciu": 0, "zutylizowana": 0}
    masa_live = 0.0
    ages: list[int] = []
    oldest = 0
    oldest_name = ""
    expired = 0
    near = 0
    by_polka: dict[str, int] = defaultdict(int)

    for f in farby:
        status = str(_row_get(f, "status", 4, "") or "")
        counts[status] = counts.get(status, 0) + 1
        waga = float(_row_get(f, "waga", 3, 0) or 0)
        name = str(_row_get(f, "pantone", 1, "") or "")
        polka = str(_row_get(f, "polka", 2, "") or "")
        data_p = _row_get(f, "data_produkcji", 5, "")
        if status in ("dostepna", "w_uzyciu"):
            masa_live += waga
            if polka:
                by_polka[polka] += 1
            age = _age_days(data_p)
            if age is not None:
                ages.append(age)
                if age > oldest:
                    oldest = age
                    oldest_name = name
                if age > 365:
                    expired += 1
                elif age >= 336:
                    near += 1

    avg_age = (sum(ages) / len(ages)) if ages else 0.0

    today = local_date_str()
    d7 = local_date_str(local_now() - timedelta(days=6))
    d30 = local_date_str(local_now() - timedelta(days=29))
    tomorrow = local_date_str(local_now() + timedelta(days=1))

    wydania_d, _ = _count_ops(cur, "wydanie", today, tomorrow)
    zwroty_d, _ = _count_ops(cur, "zwrot", today, tomorrow)
    utyl_d, utyl_masa_d = _count_ops(cur, "utylizacja", today, tomorrow)

    wydania_7, _ = _count_ops(cur, "wydanie", d7, tomorrow)
    zwroty_7, _ = _count_ops(cur, "zwrot", d7, tomorrow)
    utyl_7, utyl_masa_7 = _count_ops(cur, "utylizacja", d7, tomorrow)

    wydania_30, _ = _count_ops(cur, "wydanie", d30, tomorrow)
    zwroty_30, _ = _count_ops(cur, "zwrot", d30, tomorrow)
    utyl_30, utyl_masa_30 = _count_ops(cur, "utylizacja", d30, tomorrow)

    # Przyczyny utylizacji 30 dni
    cur.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(uwagi), ''), '(brak)') AS powod, COUNT(*) AS n,
               COALESCE(SUM(CAST(ilosc AS REAL)), 0) AS masa
        FROM operacje
        WHERE typ='utylizacja'
          AND data IS NOT NULL AND TRIM(data) != ''
          AND date(data) >= ? AND date(data) < ?
        GROUP BY powod
        ORDER BY n DESC
        """,
        (d30, tomorrow),
    )
    przyczyny = [
        {
            "powod": _row_get(r, "powod", 0, ""),
            "n": int(_row_get(r, "n", 1, 0) or 0),
            "masa": float(_row_get(r, "masa", 2, 0) or 0),
        }
        for r in cur.fetchall()
    ]

    cur.execute("SELECT polka, max_ilosc FROM magazyn_polki ORDER BY CAST(polka AS INTEGER)")
    polki_rows = cur.fetchall()
    shelves = []
    free_total = 0
    fill_sum = 0.0
    fill_n = 0
    for row in polki_rows:
        polka = str(_row_get(row, "polka", 0, "") or "")
        max_q = int(_row_get(row, "max_ilosc", 1, 0) or 0)
        used = int(by_polka.get(polka, 0))
        free = max(0, max_q - used)
        fill = (used / max_q) if max_q else 0.0
        free_total += free
        if max_q:
            fill_sum += fill
            fill_n += 1
        shelves.append(
            {
                "polka": polka,
                "max": max_q,
                "used": used,
                "free": free,
                "fill_pct": round(fill * 100, 1),
                "label": f"{used} / {max_q}",
            }
        )
    avg_fill = (fill_sum / fill_n * 100) if fill_n else 0.0

    return {
        "dostepna": counts.get("dostepna", 0),
        "w_uzyciu": counts.get("w_uzyciu", 0),
        "zutylizowana": counts.get("zutylizowana", 0),
        "masa_kg": round(masa_live, 2),
        "sredni_wiek": round(avg_age, 1),
        "najstarsza": oldest,
        "najstarsza_nazwa": oldest_name,
        "przeterminowane": expired,
        "blisko_terminu": near,
        "alert_przeterminowana": sum(
            1
            for f in farby
            if str(_row_get(f, "status", 4, "")) != "zutylizowana"
            and alert_daty(_row_get(f, "data_produkcji", 5, "")) == "przeterminowana"
        ),
        "aktywnosc": {
            "wydania": {"dzis": wydania_d, "tydzien": wydania_7, "dni30": wydania_30},
            "zwroty": {"dzis": zwroty_d, "tydzien": zwroty_7, "dni30": zwroty_30},
            "utylizacje": {
                "dzis": utyl_d,
                "tydzien": utyl_7,
                "dni30": utyl_30,
                "masa_dzis": round(utyl_masa_d, 2),
                "masa_tydzien": round(utyl_masa_7, 2),
                "masa_30d": round(utyl_masa_30, 2),
            },
        },
        "przyczyny_30d": przyczyny,
        "polki": shelves,
        "wolne_miejsca": free_total,
        "srednie_zapelnienie": round(avg_fill, 1),
        "report_date": today,
    }


def refresh_magazyn_daily_report(cur, report_date: str | None = None) -> dict[str, Any]:
    """Upsert dziennego snapshotu KPI — wywoływane przy ruchach magazynu."""

    from services.magazyn_import import ensure_magazyn_aux_tables

    ensure_magazyn_aux_tables(cur)
    data = build_magazyn_dashboard(cur)
    day = report_date or data["report_date"]
    akt = data["aktywnosc"]
    payload = json.dumps(data, ensure_ascii=False)
    now = local_datetime_str()

    # SQLite / Postgres upsert
    from db_compat import is_postgres

    if is_postgres():
        cur.execute(
            """
            INSERT INTO magazyn_daily_reports (
                report_date, dostepna, w_uzyciu, zutylizowana, masa_kg, sredni_wiek,
                najstarsza, przeterminowane, blisko_terminu,
                wydania_dzis, zwroty_dzis, utylizacje_dzis, utyl_masa_dzis,
                wydania_7d, zwroty_7d, utylizacje_7d, utyl_masa_7d,
                wydania_30d, zwroty_30d, utylizacje_30d, utyl_masa_30d,
                payload_json, updated_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            ON CONFLICT (report_date) DO UPDATE SET
                dostepna=EXCLUDED.dostepna,
                w_uzyciu=EXCLUDED.w_uzyciu,
                zutylizowana=EXCLUDED.zutylizowana,
                masa_kg=EXCLUDED.masa_kg,
                sredni_wiek=EXCLUDED.sredni_wiek,
                najstarsza=EXCLUDED.najstarsza,
                przeterminowane=EXCLUDED.przeterminowane,
                blisko_terminu=EXCLUDED.blisko_terminu,
                wydania_dzis=EXCLUDED.wydania_dzis,
                zwroty_dzis=EXCLUDED.zwroty_dzis,
                utylizacje_dzis=EXCLUDED.utylizacje_dzis,
                utyl_masa_dzis=EXCLUDED.utyl_masa_dzis,
                wydania_7d=EXCLUDED.wydania_7d,
                zwroty_7d=EXCLUDED.zwroty_7d,
                utylizacje_7d=EXCLUDED.utylizacje_7d,
                utyl_masa_7d=EXCLUDED.utyl_masa_7d,
                wydania_30d=EXCLUDED.wydania_30d,
                zwroty_30d=EXCLUDED.zwroty_30d,
                utylizacje_30d=EXCLUDED.utylizacje_30d,
                utyl_masa_30d=EXCLUDED.utyl_masa_30d,
                payload_json=EXCLUDED.payload_json,
                updated_at=EXCLUDED.updated_at
            """,
            (
                day,
                data["dostepna"],
                data["w_uzyciu"],
                data["zutylizowana"],
                data["masa_kg"],
                data["sredni_wiek"],
                data["najstarsza"],
                data["przeterminowane"],
                data["blisko_terminu"],
                akt["wydania"]["dzis"],
                akt["zwroty"]["dzis"],
                akt["utylizacje"]["dzis"],
                akt["utylizacje"]["masa_dzis"],
                akt["wydania"]["tydzien"],
                akt["zwroty"]["tydzien"],
                akt["utylizacje"]["tydzien"],
                akt["utylizacje"]["masa_tydzien"],
                akt["wydania"]["dni30"],
                akt["zwroty"]["dni30"],
                akt["utylizacje"]["dni30"],
                akt["utylizacje"]["masa_30d"],
                payload,
                now,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO magazyn_daily_reports (
                report_date, dostepna, w_uzyciu, zutylizowana, masa_kg, sredni_wiek,
                najstarsza, przeterminowane, blisko_terminu,
                wydania_dzis, zwroty_dzis, utylizacje_dzis, utyl_masa_dzis,
                wydania_7d, zwroty_7d, utylizacje_7d, utyl_masa_7d,
                wydania_30d, zwroty_30d, utylizacje_30d, utyl_masa_30d,
                payload_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(report_date) DO UPDATE SET
                dostepna=excluded.dostepna,
                w_uzyciu=excluded.w_uzyciu,
                zutylizowana=excluded.zutylizowana,
                masa_kg=excluded.masa_kg,
                sredni_wiek=excluded.sredni_wiek,
                najstarsza=excluded.najstarsza,
                przeterminowane=excluded.przeterminowane,
                blisko_terminu=excluded.blisko_terminu,
                wydania_dzis=excluded.wydania_dzis,
                zwroty_dzis=excluded.zwroty_dzis,
                utylizacje_dzis=excluded.utylizacje_dzis,
                utyl_masa_dzis=excluded.utyl_masa_dzis,
                wydania_7d=excluded.wydania_7d,
                zwroty_7d=excluded.zwroty_7d,
                utylizacje_7d=excluded.utylizacje_7d,
                utyl_masa_7d=excluded.utyl_masa_7d,
                wydania_30d=excluded.wydania_30d,
                zwroty_30d=excluded.zwroty_30d,
                utylizacje_30d=excluded.utylizacje_30d,
                utyl_masa_30d=excluded.utyl_masa_30d,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                day,
                data["dostepna"],
                data["w_uzyciu"],
                data["zutylizowana"],
                data["masa_kg"],
                data["sredni_wiek"],
                data["najstarsza"],
                data["przeterminowane"],
                data["blisko_terminu"],
                akt["wydania"]["dzis"],
                akt["zwroty"]["dzis"],
                akt["utylizacje"]["dzis"],
                akt["utylizacje"]["masa_dzis"],
                akt["wydania"]["tydzien"],
                akt["zwroty"]["tydzien"],
                akt["utylizacje"]["tydzien"],
                akt["utylizacje"]["masa_tydzien"],
                akt["wydania"]["dni30"],
                akt["zwroty"]["dni30"],
                akt["utylizacje"]["dni30"],
                akt["utylizacje"]["masa_30d"],
                payload,
                now,
            ),
        )
    return data


def list_recent_daily_reports(cur, limit: int = 30) -> list[dict[str, Any]]:
    from services.magazyn_import import ensure_magazyn_aux_tables

    ensure_magazyn_aux_tables(cur)
    cur.execute(
        """
        SELECT report_date, dostepna, w_uzyciu, zutylizowana, masa_kg,
               wydania_dzis, zwroty_dzis, utylizacje_dzis, utyl_masa_dzis
        FROM magazyn_daily_reports
        ORDER BY report_date DESC
        LIMIT ?
        """,
        (limit,),
    )
    out = []
    for row in cur.fetchall():
        out.append(
            {
                "report_date": _row_get(row, "report_date", 0),
                "dostepna": _row_get(row, "dostepna", 1),
                "w_uzyciu": _row_get(row, "w_uzyciu", 2),
                "zutylizowana": _row_get(row, "zutylizowana", 3),
                "masa_kg": _row_get(row, "masa_kg", 4),
                "wydania_dzis": _row_get(row, "wydania_dzis", 5),
                "zwroty_dzis": _row_get(row, "zwroty_dzis", 6),
                "utylizacje_dzis": _row_get(row, "utylizacje_dzis", 7),
                "utyl_masa_dzis": _row_get(row, "utyl_masa_dzis", 8),
            }
        )
    return out
