"""Import magazynu farb z Excela (wzór: Magazyn_farb_resztkowych.xlsm).

- arkusz Magazyn → pełny stan (dostępna / w użyciu / zutylizowana)
- arkusz Baza → historia ruchów → operacje
- arkusz Półki → pojemności półek (dashboard)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore


STATUS_MAP = {
    "DOSTĘPNA": "dostepna",
    "DOSTEPNA": "dostepna",
    "W UŻYCIU": "w_uzyciu",
    "W UZYCIU": "w_uzyciu",
    "ZUTYLIZOWANA": "zutylizowana",
    "ZUTYLIZOWANE": "zutylizowana",
}
# Baza (historia) → typ w operacje — jak w Dashboard Excela.
BAZA_TYP_MAP = {
    "dostepna": "zwrot",
    "w_uzyciu": "wydanie",
    "zutylizowana": "utylizacja",
}
MISSING_DATE = "0"
SHEET_MAGAZYN = ("Magazyn", "magazyn", "MAGAZYN")
SHEET_BAZA = ("Baza", "baza", "BAZA", "BazaDanych")
SHEET_POLKI = ("Półki", "Polki", "półki", "polki")


@dataclass
class MagazynImportReport:
    sheet: str = ""
    baza_sheet: str = ""
    polki_sheet: str = ""
    rows_read: int = 0
    imported: int = 0
    disposed: int = 0
    skipped_empty: int = 0
    skipped_status: int = 0
    without_lub: int = 0
    without_artwork: int = 0
    zero_weight: int = 0
    zero_date: int = 0
    baza_rows: int = 0
    baza_imported: int = 0
    polki_imported: int = 0
    replaced: bool = True
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        parts = [
            f"Magazyn ({self.sheet or '—'})",
            f"stan={self.imported}",
            f"zutylizowane={self.disposed}",
        ]
        if self.baza_imported:
            parts.append(f"Baza operacji={self.baza_imported}")
        if self.polki_imported:
            parts.append(f"półki={self.polki_imported}")
        if self.without_lub:
            parts.append(f"bez LUB={self.without_lub}")
        if self.without_artwork:
            parts.append(f"bez artwork={self.without_artwork}")
        if self.zero_weight:
            parts.append(f"waga 0={self.zero_weight}")
        if self.zero_date:
            parts.append(f"data 0={self.zero_date}")
        if self.skipped_empty:
            parts.append(f"puste={self.skipped_empty}")
        if self.skipped_status:
            parts.append(f"inny status={self.skipped_status}")
        return " · ".join(parts)


def ensure_farby_artwork_column(cur) -> None:
    """Dodaje kolumnę artwork i pozwala na data_produkcji='0' (TEXT)."""

    from db_compat import is_postgres

    if is_postgres():
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='farby'
            """
        )
        cols = {row["column_name"]: row["data_type"] for row in cur.fetchall()}
        if "artwork" not in cols:
            cur.execute("ALTER TABLE farby ADD COLUMN artwork TEXT")
        dtype = (cols.get("data_produkcji") or "").lower()
        if dtype and dtype != "text" and "char" not in dtype:
            cur.execute(
                "ALTER TABLE farby ALTER COLUMN data_produkcji TYPE TEXT USING data_produkcji::text"
            )
    else:
        cur.execute("PRAGMA table_info(farby)")
        existing = {row[1] for row in cur.fetchall()}
        if "artwork" not in existing:
            cur.execute("ALTER TABLE farby ADD COLUMN artwork TEXT")

    ensure_magazyn_aux_tables(cur)


def ensure_magazyn_aux_tables(cur) -> None:
    """Półki + codzienne snapshoty dashboardu."""

    from db_compat import is_postgres

    pk = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS magazyn_polki (
            id {pk},
            polka TEXT NOT NULL UNIQUE,
            max_ilosc INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS magazyn_daily_reports (
            id {pk},
            report_date TEXT NOT NULL UNIQUE,
            dostepna INTEGER NOT NULL DEFAULT 0,
            w_uzyciu INTEGER NOT NULL DEFAULT 0,
            zutylizowana INTEGER NOT NULL DEFAULT 0,
            masa_kg REAL NOT NULL DEFAULT 0,
            sredni_wiek REAL NOT NULL DEFAULT 0,
            najstarsza INTEGER NOT NULL DEFAULT 0,
            przeterminowane INTEGER NOT NULL DEFAULT 0,
            blisko_terminu INTEGER NOT NULL DEFAULT 0,
            wydania_dzis INTEGER NOT NULL DEFAULT 0,
            zwroty_dzis INTEGER NOT NULL DEFAULT 0,
            utylizacje_dzis INTEGER NOT NULL DEFAULT 0,
            utyl_masa_dzis REAL NOT NULL DEFAULT 0,
            wydania_7d INTEGER NOT NULL DEFAULT 0,
            zwroty_7d INTEGER NOT NULL DEFAULT 0,
            utylizacje_7d INTEGER NOT NULL DEFAULT 0,
            utyl_masa_7d REAL NOT NULL DEFAULT 0,
            wydania_30d INTEGER NOT NULL DEFAULT 0,
            zwroty_30d INTEGER NOT NULL DEFAULT 0,
            utylizacje_30d INTEGER NOT NULL DEFAULT 0,
            utyl_masa_30d REAL NOT NULL DEFAULT 0,
            payload_json TEXT,
            updated_at TEXT
        )
        """
    )


def _norm_header(value: Any) -> str:
    return str(value or "").strip().lower().replace(".", "").replace(" ", "")


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _parse_weight(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_date(value: Any) -> str:
    if value is None or value == "":
        return MISSING_DATE
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text in ("0", "None", "-"):
        return MISSING_DATE
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10] if fmt.startswith("%Y") else text, fmt).date().isoformat()
        except ValueError:
            continue
    return MISSING_DATE


def _parse_datetime(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d 00:00")
    text = str(value).strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return text[:16]


def _map_header_values(values: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    aliases = {
        "polka": "polka",
        "półka": "polka",
        "nazwa": "nazwa",
        "pantone": "nazwa",
        "lub": "lub",
        "artwork": "artwork",
        "data": "data",
        "dataprodukcji": "data",
        "dataproduction": "data",
        "waga": "waga",
        "status": "status",
        "przyczyna": "przyczyna",
        "przyczyna2": "przyczyna2",
        "id": "id",
        "datawpisu": "data_wpisu",
        "uwagi": "uwagi",
        "maksilość": "max_ilosc",
        "maksilosc": "max_ilosc",
        "maks": "max_ilosc",
    }
    for idx, raw in enumerate(values, start=1):
        key = _norm_header(raw)
        # „data produkcji” vs „data wpisu”
        raw_l = str(raw or "").strip().lower()
        if "wpisu" in raw_l:
            field = "data_wpisu"
        elif "produkcji" in raw_l:
            field = "data"
        else:
            field = aliases.get(key)
        if field and field not in mapping:
            mapping[field] = idx
    return mapping


def _find_sheet(wb, candidates: tuple[str, ...]) -> str | None:
    lower = {n.lower(): n for n in wb.sheetnames}
    for name in candidates:
        if name in wb.sheetnames:
            return name
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def parse_magazyn_rows(content: bytes) -> tuple[list[dict[str, Any]], MagazynImportReport]:
    report = MagazynImportReport()
    if load_workbook is None:
        report.errors.append("Brak openpyxl — nie można czytać Excela")
        return [], report

    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True, keep_vba=False)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"Nie można otworzyć Excela: {exc}"[:200])
        return [], report

    sheet_name = _find_sheet(wb, SHEET_MAGAZYN)
    if not sheet_name:
        report.errors.append("Brak arkusza Magazyn")
        wb.close()
        return [], report

    ws = wb[sheet_name]
    report.sheet = sheet_name
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    headers = _map_header_values(list(header_row or []))
    if "nazwa" not in headers or "status" not in headers:
        report.errors.append("Arkusz Magazyn: brak kolumn Nazwa/Status")
        wb.close()
        return [], report

    rows: list[dict[str, Any]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        def val(field: str):
            idx = headers.get(field)
            if not idx:
                return None
            return row[idx - 1] if idx - 1 < len(row) else None

        nazwa = _cell_str(val("nazwa"))
        if not nazwa:
            report.skipped_empty += 1
            continue
        report.rows_read += 1
        status_raw = _cell_str(val("status")).upper()
        status = STATUS_MAP.get(status_raw)
        if not status:
            report.skipped_status += 1
            continue
        if status == "zutylizowana":
            report.disposed += 1

        lub = _cell_str(val("lub"))
        artwork = _cell_str(val("artwork"))
        polka = _cell_str(val("polka"))
        waga = _parse_weight(val("waga"))
        data_produkcji = _parse_date(val("data"))
        przyczyna = _cell_str(val("przyczyna"))
        if not lub:
            report.without_lub += 1
        if not artwork:
            report.without_artwork += 1
        if waga == 0.0:
            report.zero_weight += 1
        if data_produkcji == MISSING_DATE:
            report.zero_date += 1

        rows.append(
            {
                "pantone": nazwa,
                "lub": lub,
                "artwork": artwork,
                "polka": polka,
                "waga": waga,
                "status": status,
                "data_produkcji": data_produkcji,
                "przyczyna": przyczyna,
            }
        )

    wb.close()
    return rows, report


def parse_baza_rows(content: bytes) -> tuple[list[dict[str, Any]], str]:
    if load_workbook is None:
        return [], ""
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True, keep_vba=False)
    except Exception:  # noqa: BLE001
        return [], ""
    sheet_name = _find_sheet(wb, SHEET_BAZA)
    if not sheet_name:
        wb.close()
        return [], ""
    ws = wb[sheet_name]
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    headers = _map_header_values(list(header_row or []))
    if "nazwa" not in headers or "status" not in headers:
        wb.close()
        return [], sheet_name

    rows: list[dict[str, Any]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        def val(field: str):
            idx = headers.get(field)
            if not idx:
                return None
            return row[idx - 1] if idx - 1 < len(row) else None

        nazwa = _cell_str(val("nazwa"))
        if not nazwa:
            continue
        status = STATUS_MAP.get(_cell_str(val("status")).upper())
        if not status:
            continue
        typ = BAZA_TYP_MAP[status]
        uwagi = _cell_str(val("uwagi")) or _cell_str(val("przyczyna"))
        rows.append(
            {
                "data": _parse_datetime(val("data_wpisu")) or _parse_datetime(val("data")) or "",
                "typ": typ,
                "farba": nazwa,
                "ilosc": str(_parse_weight(val("waga"))),
                "polka": _cell_str(val("polka")),
                "uwagi": uwagi,
            }
        )
    wb.close()
    return rows, sheet_name


def parse_polki_rows(content: bytes) -> tuple[list[dict[str, Any]], str]:
    if load_workbook is None:
        return [], ""
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True, keep_vba=False)
    except Exception:  # noqa: BLE001
        return [], ""
    sheet_name = _find_sheet(wb, SHEET_POLKI)
    if not sheet_name:
        wb.close()
        return [], ""
    ws = wb[sheet_name]
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    headers = _map_header_values(list(header_row or []))
    rows: list[dict[str, Any]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        polka = _cell_str(row[0] if row else None)
        if not polka:
            continue
        try:
            int(polka)
        except ValueError:
            # stop at summary / DATA section
            if not polka.isdigit():
                break
        max_raw = row[1] if len(row) > 1 else 0
        try:
            max_ilosc = int(float(max_raw or 0))
        except (TypeError, ValueError):
            max_ilosc = 0
        if max_ilosc <= 0:
            continue
        rows.append({"polka": polka, "max_ilosc": max_ilosc})
    wb.close()
    return rows, sheet_name


def import_magazyn_excel(cur, content: bytes, *, imported_by: str = "admin") -> MagazynImportReport:
    """Replace stanu magazynu + historii Baza + pojemności półek."""

    from helpers import dodaj_operacje
    from services.magazyn_dashboard import refresh_magazyn_daily_report

    ensure_farby_artwork_column(cur)
    rows, report = parse_magazyn_rows(content)
    if report.errors:
        return report

    baza_rows, baza_sheet = parse_baza_rows(content)
    polki_rows, polki_sheet = parse_polki_rows(content)
    report.baza_sheet = baza_sheet
    report.polki_sheet = polki_sheet
    report.baza_rows = len(baza_rows)

    try:
        cur.execute("DELETE FROM farba_lub_assignments")
    except Exception:  # noqa: BLE001
        pass
    cur.execute("DELETE FROM farby")
    # Pełny replace historii farb z arkusza Baza (Excel = źródło prawdy przy migracji).
    cur.execute("DELETE FROM operacje")

    if polki_rows:
        cur.execute("DELETE FROM magazyn_polki")
        for item in polki_rows:
            cur.execute(
                "INSERT INTO magazyn_polki (polka, max_ilosc) VALUES (?, ?)",
                (item["polka"], item["max_ilosc"]),
            )
            report.polki_imported += 1

    for item in rows:
        cur.execute(
            """
            INSERT INTO farby (pantone, lub, artwork, polka, waga, status, data_produkcji)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["pantone"],
                item["lub"] or None,
                item["artwork"] or None,
                item["polka"] or None,
                item["waga"],
                item["status"],
                item["data_produkcji"],
            ),
        )
        report.imported += 1

    if baza_rows:
        for op in baza_rows:
            cur.execute(
                """
                INSERT INTO operacje (data, typ, farba, ilosc, polka, uwagi, farba_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op["data"] or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    op["typ"],
                    op["farba"],
                    op["ilosc"],
                    op["polka"],
                    op["uwagi"] or "",
                    None,
                ),
            )
            report.baza_imported += 1
    else:
        # Bez Bazy: dla zutylizowanych zapisz przyczynę z Magazynu do historii.
        for item in rows:
            if item["status"] != "zutylizowana":
                continue
            cur.execute(
                """
                INSERT INTO operacje (data, typ, farba, ilosc, polka, uwagi, farba_id)
                VALUES (?, 'utylizacja', ?, ?, ?, ?, NULL)
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    item["pantone"],
                    str(item["waga"]),
                    item["polka"] or "",
                    item.get("przyczyna") or "",
                ),
            )

    dodaj_operacje(
        cur,
        "import",
        "MAGAZYN",
        str(report.imported),
        "",
        f"Replace magazynu z Excela ({imported_by}): {report.as_text()}",
        None,
    )
    try:
        refresh_magazyn_daily_report(cur)
    except Exception:  # noqa: BLE001
        pass
    return report
