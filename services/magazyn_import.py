"""Import żywego magazynu farb z Excela (wzór: Magazyn_farb_resztkowych.xlsm)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore


LIVE_STATUSES = {
    "DOSTĘPNA": "dostepna",
    "DOSTEPNA": "dostepna",
    "W UŻYCIU": "w_uzyciu",
    "W UZYCIU": "w_uzyciu",
}
SKIP_STATUSES = {"ZUTYLIZOWANA", "ZUTYLIZOWANE"}
MISSING_DATE = "0"
SHEET_CANDIDATES = ("Magazyn", "magazyn", "MAGAZYN")


@dataclass
class MagazynImportReport:
    sheet: str = ""
    rows_read: int = 0
    imported: int = 0
    skipped_disposed: int = 0
    skipped_empty: int = 0
    skipped_status: int = 0
    without_lub: int = 0
    without_artwork: int = 0
    zero_weight: int = 0
    zero_date: int = 0
    replaced: bool = True
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        parts = [
            f"Magazyn Excel ({self.sheet or '—'})",
            f"wiersze={self.rows_read}",
            f"zaimportowano={self.imported}",
            f"zutylizowane pominięte={self.skipped_disposed}",
        ]
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
        return

    cur.execute("PRAGMA table_info(farby)")
    existing = {row[1] for row in cur.fetchall()}
    if "artwork" not in existing:
        cur.execute("ALTER TABLE farby ADD COLUMN artwork TEXT")


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


def _map_header_row(ws) -> dict[str, int]:
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
        "waga": "waga",
        "status": "status",
    }
    for col in range(1, min(ws.max_column or 1, 20) + 1):
        key = _norm_header(ws.cell(1, col).value)
        field = aliases.get(key)
        if field and field not in mapping:
            mapping[field] = col
    return mapping


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

    sheet_name = next((n for n in SHEET_CANDIDATES if n in wb.sheetnames), None)
    if not sheet_name:
        report.errors.append("Brak arkusza Magazyn")
        wb.close()
        return [], report

    ws = wb[sheet_name]
    report.sheet = sheet_name
    headers = _map_header_row(ws)
    if "nazwa" not in headers or "status" not in headers:
        report.errors.append("Arkusz Magazyn: brak kolumn Nazwa/Status")
        wb.close()
        return [], report

    rows: list[dict[str, Any]] = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        def val(field: str):
            idx = headers.get(field)
            if not idx:
                return None
            return row[idx - 1].value if idx - 1 < len(row) else None

        nazwa = _cell_str(val("nazwa"))
        if not nazwa:
            report.skipped_empty += 1
            continue
        report.rows_read += 1
        status_raw = _cell_str(val("status")).upper()
        if status_raw in SKIP_STATUSES:
            report.skipped_disposed += 1
            continue
        status = LIVE_STATUSES.get(status_raw)
        if not status:
            report.skipped_status += 1
            continue

        lub = _cell_str(val("lub"))
        artwork = _cell_str(val("artwork"))
        polka = _cell_str(val("polka"))
        waga = _parse_weight(val("waga"))
        data_produkcji = _parse_date(val("data"))
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
            }
        )

    wb.close()
    return rows, report


def import_magazyn_excel(cur, content: bytes, *, imported_by: str = "admin") -> MagazynImportReport:
    """Replace żywego magazynu farb danymi z arkusza Magazyn."""

    from helpers import dodaj_operacje

    ensure_farby_artwork_column(cur)
    rows, report = parse_magazyn_rows(content)
    if report.errors:
        return report

    # Replace: czyścimy przypisania LUB i stan farb (historia operacji zostaje).
    try:
        cur.execute("DELETE FROM farba_lub_assignments")
    except Exception:  # noqa: BLE001
        pass
    cur.execute("DELETE FROM farby")

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

    dodaj_operacje(
        cur,
        "import",
        "MAGAZYN",
        str(report.imported),
        "",
        f"Replace magazynu farb z Excela ({imported_by}): {report.as_text()}",
        None,
    )
    return report
