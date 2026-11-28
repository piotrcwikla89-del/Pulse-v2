"""
Pomocnicze funkcje współdzielone przez wszystkie moduły aplikacji.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from starlette.requests import Request

from db_compat import is_postgres
from time_utils import format_local_datetime, local_datetime_str, local_now, local_today, utc_now_db_string

# ==================== ŚCIEŻKI ====================

def get_base_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)


def get_db_path() -> str:
    return os.path.join(get_base_path(), "database.db")


def get_templates_path() -> str:
    return os.path.join(get_base_path(), "templates")


def get_resources_path() -> str:
    """Zwraca ścieżkę do folderu zasobów (CSV, assets itp)."""
    base = get_base_path()
    resources = os.path.join(base, "resources")
    if os.path.isdir(resources):
        return resources
    return base


# ==================== JINJA2 ====================

env: Environment | None = None


def init_jinja_env() -> None:
    global env
    if env is None:
        env = Environment(
            loader=FileSystemLoader(get_templates_path()),
            autoescape=True,
            cache_size=0,
        )
        env.filters["localdatetime"] = format_local_datetime
        env.filters["lub"] = format_lub_label

        def _tojson(value):
            return Markup(json.dumps(value, ensure_ascii=False))

        env.filters["tojson"] = _tojson


def render_template(name: str, context: dict) -> HTMLResponse:
    init_jinja_env()
    template = env.get_template(name)
    return HTMLResponse(template.render(context))


# ==================== DB HELPERY ====================

def dodaj_operacje(cur, typ, farba, ilosc, polka, uwagi="", farba_id=None):
    cur.execute(
        """
        INSERT INTO operacje (data, typ, farba, ilosc, polka, uwagi, farba_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (local_datetime_str(fmt="%Y-%m-%d %H:%M"), typ, farba, ilosc, polka, uwagi, farba_id),
    )
    # Auto-snapshot KPI magazynu (dashboard / raporty dzienne).
    if typ in (
        "wydanie",
        "zwrot",
        "utylizacja",
        "przyjęcie",
        "przywrócenie",
        "import",
        "edycja",
    ):
        try:
            from services.magazyn_dashboard import refresh_magazyn_daily_report

            refresh_magazyn_daily_report(cur)
        except Exception:  # noqa: BLE001
            pass


def log_production_operation(cur, operation_type, description, machine=None, plan_id=None, user="system"):
    cur.execute(
        """
        INSERT INTO production_log (operation_type, description, machine, plan_id, user, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (operation_type, description, machine, plan_id, user, utc_now_db_string()),
    )


# ==================== DATY / ALERTY ====================

def parse_production_date(data_val):
    """Parsuje datę produkcji farby; sentinel ``0`` / pusta → None."""
    if not data_val:
        return None
    if isinstance(data_val, datetime):
        return data_val.date()
    if isinstance(data_val, date):
        return data_val
    text = str(data_val).strip()
    if not text or text in ("0", "-", "None", "null"):
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def alert_daty_as_of(data_val, as_of=None) -> str:
    """Alert wieku farby względem wskazanego dnia (domyślnie dziś)."""
    d = parse_production_date(data_val)
    if not d:
        return "ok"
    ref = as_of or local_today()
    if isinstance(ref, datetime):
        ref = ref.date()
    dni = (ref - d).days
    if dni > 365:
        return "przeterminowana"
    if dni > 275:
        return "uwaga"
    return "ok"


def alert_daty(data_val) -> str:
    """Akceptuje str (SQLite) lub date/datetime (PostgreSQL / sterowniki DB).

    Sentinel ``0`` / pusta data = brak daty produkcji (bez alertu).
    """
    return alert_daty_as_of(data_val, as_of=local_today())


def ink_status_on_print_day(data_produkcji, print_day) -> dict:
    """Data produkcji farby + wiek / alert na dzień zadruku pracy."""
    prod = parse_production_date(data_produkcji)
    ref = print_day
    if isinstance(ref, datetime):
        ref = ref.date()
    age_days = None
    if prod and ref:
        age_days = (ref - prod).days
    return {
        "data_produkcji": prod.isoformat() if prod else (None if data_produkcji in (None, "", "0", 0) else str(data_produkcji)),
        "print_day": ref.isoformat() if ref else None,
        "age_days_on_print": age_days,
        "alert_on_print": alert_daty_as_of(data_produkcji, as_of=ref) if ref else alert_daty(data_produkcji),
    }


# ==================== REDIRECT URL ====================

def build_redirect_url(request: Request, extra_params: dict | None = None) -> str:
    params = dict(request.query_params)
    if extra_params:
        params.update(extra_params)
    params = {k: v for k, v in params.items() if v}
    query = urlencode(params)
    return f"/magazyn?{query}" if query else "/magazyn"


# ==================== FILTROWANIE FARB ====================

def filtruj_farby(dane, search_field: str, search_value: str, filtr_alert: str, status: str) -> list:
    """Filtruje rekordy farb i zwraca listę słowników z kluczem 'alert'."""
    farby = []
    licznik_przeterminowane = 0
    licznik_uwaga = 0
    licznik_zutylizowane = 0

    for f in dane:
        alert = alert_daty(f["data_produkcji"])
        if f["status"] != "zutylizowana":
            if alert == "przeterminowana":
                licznik_przeterminowane += 1
            elif alert == "uwaga":
                licznik_uwaga += 1
        else:
            licznik_zutylizowane += 1

        if filtr_alert and alert != filtr_alert:
            continue
        if status and f["status"] != status:
            continue
        if search_value:
            if search_field == "lub" and search_value.lower() not in (f["lub"] or "").lower():
                continue
            elif search_field == "pantone" and search_value.lower() not in f["pantone"].lower():
                continue
            elif search_field == "polka" and search_value.lower() not in (f["polka"] or "").lower():
                continue
            elif search_field == "artwork" and search_value.lower() not in (f.get("artwork") or "").lower():
                continue

        farby.append({**dict(f), "alert": alert})

    return farby, licznik_przeterminowane, licznik_uwaga, licznik_zutylizowane


# ==================== POWIADOMIENIA ====================

NOTIFICATION_EVENT_LABELS = {
    "CALL_MANAGER": "Wezwanie kierownika",
    "CALL_MIXING": "Wezwanie operatora mieszalni",
    "CALL_PREPRESS": "Wezwanie prepress",
    "PROBLEM_REPORT": "Zgłoszenie problemu na maszynie",
    "REPORT_PRINT_CALLED": "Wywołanie raportu zadruku (powiadomienie do kierownika)",
    "REPORT_PRODUCTION_CALLED": "Wywołanie raportu produkcji (powiadomienie do kierownika)",
    "JOB_STARTED": "Rozpoczęcie zlecenia (powiadomienie do kierownika)",
    "JOB_COMPLETED": "Zakończenie zlecenia (powiadomienie do kierownika)",
    "ASSORTMENT_CONFIRMED": "Zatwierdzenie asortymentu (powiadomienie do kierownika)",
    "RAPORT_ZADRUKU_ZAPISANY": "Zapisany raport kontroli zadruku (powiadomienie)",
    "RAPORT_PRODUKCJI_ZAPISANY": "Zapisany raport produkcji (powiadomienie)",
    "WINDING_REPORT_SUBMITTED": "Zapisany raport przewijania (powiadomienie)",
}

PROBLEM_CATEGORY_DEFINITIONS = (
    ("farby", "Farby", "operator_mieszalni", 1),
    ("polimery", "Polimery", "prepress", 2),
    ("laminat", "Laminat", "manager", 3),
    ("brak_pomocnika", "Brak pomocnika", "manager", 4),
    ("problemy_techniczne_maszyna", "Problemy techniczne na maszynie", "manager", 5),
)


def is_notification_enabled(cur, event_key: str) -> bool:
    cur.execute("SELECT enabled FROM notification_settings WHERE event_key=?", (event_key,))
    row = cur.fetchone()
    if row is None:
        return True
    return bool(row["enabled"])


def insert_notification_if_enabled(cur, event_key: str, machine, plan_id, message, target_role, created_by):
    if not is_notification_enabled(cur, event_key):
        return
    try:
        cur.execute(
            """
            INSERT INTO notifications (machine, plan_id, message, target_role, created_by, event_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (machine, plan_id, message, target_role, created_by, event_key),
        )
    except Exception:
        # Starsze schematy bez kolumny event_key
        cur.execute(
            "INSERT INTO notifications (machine, plan_id, message, target_role, created_by) VALUES (?, ?, ?, ?, ?)",
            (machine, plan_id, message, target_role, created_by),
        )


def log_domain_event(cur, event_type: str, actor_user: str, machine=None, plan_id=None, lub_number=None, payload=None):
    cur.execute(
        """INSERT INTO events (event_type, actor_user, machine, plan_id, lub_number, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_type, actor_user, machine, plan_id, lub_number, payload, utc_now_db_string()),
    )


def seed_notification_settings_rows(cur):
    for key in NOTIFICATION_EVENT_LABELS:
        cur.execute(
            "INSERT OR IGNORE INTO notification_settings (event_key, enabled) VALUES (?, 1)",
            (key,),
        )


def seed_problem_categories(cur):
    for code, label, target_role, sort_order in PROBLEM_CATEGORY_DEFINITIONS:
        cur.execute(
            """
            INSERT INTO problem_categories (code, label, target_role, visible_for_manager, is_active, sort_order)
            VALUES (?, ?, ?, 1, 1, ?)
            ON CONFLICT(code) DO NOTHING
            """,
            (code, label, target_role, sort_order),
        )


def get_edit_password(cur) -> str:
    """Zwraca aktualne hasło edycji z system_settings (domyślnie 'haslo')."""
    try:
        cur.execute("SELECT value FROM system_settings WHERE key='edit_password'")
        row = cur.fetchone()
        return row["value"] if row else "haslo"
    except Exception:
        return "haslo"


# ==================== MASZYNY / PLANY ====================

PRODUCTION_MACHINES = ("D6", "D8", "D10")
WINDING_MACHINES = ("P1", "P2")


def format_lub_label(lub_number) -> str:
    """Jedna etykieta LUB — bez podwójnego prefiksu (LUB LUB4475 → LUB4475)."""
    if lub_number is None:
        return "brak"
    text = str(lub_number).strip()
    if not text:
        return "brak"
    if text.upper().startswith("LUB"):
        return text
    return f"LUB{text}" if text[:1].isdigit() else f"LUB {text}"


def _coerce_qty(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return value
    raw = str(value).strip().replace(" ", "").replace(",", ".")
    if not raw:
        return 0
    try:
        num = float(raw)
    except ValueError:
        return 0
    return int(num) if num.is_integer() else num


def plan_production_targets(plan) -> dict:
    """Planowane metry (T / meters) i sztuki (W / pieces) dla widoku zlecenia."""
    def _get(key):
        if plan is None:
            return None
        try:
            return plan[key]
        except Exception:
            if isinstance(plan, dict):
                return plan.get(key)
            return None

    meters = _coerce_qty(_get("raw_web_used") or _get("meters"))
    pieces = _coerce_qty(_get("wbpr_qty") or _get("pieces"))
    return {"meters": meters, "pieces": pieces}


def normalize_yes_no(value) -> str:
    """Normalizuje odpowiedź tak/nie z formularza kontroli zadruku."""
    raw = str(value or "").strip().lower()
    if raw in ("tak", "yes", "true", "1", "ok"):
        return "tak"
    if raw in ("nie", "no", "false", "0", "nok"):
        return "nie"
    return ""


def normalize_barcode_grade(value) -> str:
    """Ocena czytelności kodów kreskowych: A–E."""
    grade = str(value or "").strip().upper()
    if grade in ("A", "B", "C", "D", "E"):
        return grade
    return ""


def print_control_status_from_answers(dimensions_match: str, colors_match: str, barcode_grade: str) -> str:
    """Status kontroli: OK tylko gdy wymiary i kolory = tak oraz barcode A–C."""
    dims = normalize_yes_no(dimensions_match)
    colors = normalize_yes_no(colors_match)
    grade = normalize_barcode_grade(barcode_grade)
    if dims == "tak" and colors == "tak" and grade in ("A", "B", "C"):
        return "OK"
    return "NOT_OK"


def sum_print_control_rolls(cur, plan_id: int) -> dict:
    """Agreguje metry/sztuki z raportów kontroli zadruku (rolka po rolce) pod postęp i raport końcowy."""
    cur.execute(
        """
        SELECT
            COUNT(*) AS roll_count,
            COALESCE(SUM(roll_meters), 0) AS meters,
            COALESCE(SUM(roll_pieces), 0) AS pieces,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(status, '')) = 'OK' THEN roll_pieces ELSE 0 END), 0) AS ok_pieces,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(status, '')) != 'OK' THEN roll_pieces ELSE 0 END), 0) AS nok_pieces,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(status, '')) = 'OK' THEN roll_meters ELSE 0 END), 0) AS ok_meters,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(status, '')) != 'OK' THEN roll_meters ELSE 0 END), 0) AS nok_meters
        FROM print_control_reports
        WHERE plan_id=?
        """,
        (plan_id,),
    )
    row = cur.fetchone()
    if not row:
        return {
            "roll_count": 0,
            "meters": 0.0,
            "pieces": 0,
            "ok_pieces": 0,
            "nok_pieces": 0,
            "ok_meters": 0.0,
            "nok_meters": 0.0,
        }

    def _num(key, default=0):
        try:
            val = row[key]
        except Exception:
            val = None
        if val is None:
            return default
        try:
            return float(val) if isinstance(default, float) else int(val)
        except (TypeError, ValueError):
            return default

    return {
        "roll_count": _num("roll_count", 0),
        "meters": float(_num("meters", 0.0)),
        "pieces": int(_num("pieces", 0)),
        "ok_pieces": int(_num("ok_pieces", 0)),
        "nok_pieces": int(_num("nok_pieces", 0)),
        "ok_meters": float(_num("ok_meters", 0.0)),
        "nok_meters": float(_num("nok_meters", 0.0)),
    }


def normalize_shift_label(shift_val: str) -> str:
    if not shift_val:
        return "dzien"
    s = str(shift_val).lower().strip()
    if s in ("1", "dzien", "dzień", "day"):
        return "dzien"
    if s in ("noc", "night", "2"):
        return "noc"
    return s


def _parse_shift_time(value) -> tuple[int, int] | None:
    if value is None:
        return None
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return int(value.hour), int(value.minute)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parts = raw.split(":")
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def resolve_active_shift(cur, now_local: datetime | None = None) -> tuple[str, str]:
    current_local = now_local or local_now()
    current_minutes = current_local.hour * 60 + current_local.minute
    shift_rows = []
    try:
        cur.execute("SELECT name, start_time, end_time FROM shifts")
        shift_rows = cur.fetchall()
    except Exception:
        shift_rows = []

    for row in shift_rows:
        shift_norm = normalize_shift_label(row["name"])
        start_parts = _parse_shift_time(row["start_time"])
        end_parts = _parse_shift_time(row["end_time"])
        if start_parts is None or end_parts is None:
            continue
        start_minutes = start_parts[0] * 60 + start_parts[1]
        end_minutes = end_parts[0] * 60 + end_parts[1]
        crosses_midnight = start_minutes >= end_minutes
        if not crosses_midnight and start_minutes <= current_minutes < end_minutes:
            return shift_norm, current_local.strftime("%Y-%m-%d")
        if crosses_midnight and (current_minutes >= start_minutes or current_minutes < end_minutes):
            shift_date = current_local.date()
            if current_minutes < end_minutes:
                shift_date = shift_date - timedelta(days=1)
            return shift_norm, shift_date.strftime("%Y-%m-%d")

    fallback_shift = "dzien" if 6 <= current_local.hour < 18 else "noc"
    fallback_date = current_local.date()
    if fallback_shift == "noc" and current_local.hour < 6:
        fallback_date = fallback_date - timedelta(days=1)
    return fallback_shift, fallback_date.strftime("%Y-%m-%d")


def find_pending_machine_handover(cur, machine: str, now_local: datetime | None = None) -> dict | None:
    active_shift, handover_date = resolve_active_shift(cur, now_local=now_local)
    cur.execute(
        """
        SELECT sh.*, incoming.name AS incoming_shift_name, outgoing.name AS outgoing_shift_name
        FROM shift_handovers sh
        JOIN shifts incoming ON incoming.id = sh.incoming_shift_id
        JOIN shifts outgoing ON outgoing.id = sh.outgoing_shift_id
        WHERE sh.machine=? AND sh.handover_date=? AND sh.status='waiting_ack'
        ORDER BY sh.id DESC
        """,
        (machine.upper(), handover_date),
    )
    for row in cur.fetchall():
        handover = dict(row)
        if normalize_shift_label(handover["incoming_shift_name"]) == active_shift:
            handover["active_shift"] = active_shift
            handover["reference_date"] = handover_date
            return handover
    return None


def find_pending_role_shift_handover(cur, role: str, now_local: datetime | None = None) -> dict | None:
    active_shift, handover_date = resolve_active_shift(cur, now_local=now_local)
    cur.execute(
        """
        SELECT rsh.*, incoming.name AS incoming_shift_name, outgoing.name AS outgoing_shift_name
        FROM role_shift_handovers rsh
        JOIN shifts incoming ON incoming.id = rsh.incoming_shift_id
        JOIN shifts outgoing ON outgoing.id = rsh.outgoing_shift_id
        WHERE rsh.role=? AND rsh.handover_date=? AND rsh.status='waiting_ack'
        ORDER BY rsh.id DESC
        """,
        (role, handover_date),
    )
    for row in cur.fetchall():
        handover = dict(row)
        if normalize_shift_label(handover["incoming_shift_name"]) == active_shift:
            handover["active_shift"] = active_shift
            handover["reference_date"] = handover_date
            return handover
    return None


def has_pending_role_handover(cur, role: str) -> bool:
    if find_pending_role_shift_handover(cur, role):
        return True
    cur.execute(
        """
        SELECT 1
                FROM production_report_issues pri
                JOIN problem_categories pc ON pc.id = pri.problem_category_id
                WHERE pc.target_role=?
                    AND COALESCE(pri.needs_handover, 1)=1
                    AND COALESCE(pri.status, 'new') != 'resolved'
        LIMIT 1
        """,
        (role,),
    )
    return cur.fetchone() is not None


def resolve_plan_id_for_job(cur, machine: str, job_number: str):
    cur.execute(
        """SELECT id FROM production_plans
           WHERE order_number=? AND machine=? AND status IN ('planned','in_progress')
           ORDER BY id DESC LIMIT 1""",
        (job_number, machine.upper()),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def normalize_lub_key(value) -> str:
    """Kanoniczny klucz LUB: LUB4475 / 4475 / 'LUB 4475' → '4475'."""
    if value is None:
        return ""
    text = str(value).strip().upper().replace(" ", "").replace("-", "")
    if text.startswith("LUB"):
        text = text[3:]
    return text


def lub_match_variants(value) -> list[str]:
    """Warianty zapisu LUB do zapytań SQL (dokładne dopasowanie + normalizacja)."""
    raw = str(value or "").strip()
    key = normalize_lub_key(raw)
    variants: list[str] = []
    for item in (
        raw,
        key,
        f"LUB{key}",
        f"LUB {key}",
        f"LUB-{key}",
        f"lub{key}",
        f"Lub{key}",
        f"lub-{key}",
    ):
        if item and item not in variants:
            variants.append(item)
    return variants


def enrich_plans_with_lub_materials(cur, plan_rows):
    """Do każdego planu dokleja listy farb i polimerów wg lub_number (dla widoku operator/prepress)."""
    if not plan_rows:
        return []
    lubs = {p["lub_number"] for p in plan_rows if p["lub_number"]}
    farby_by_lub = defaultdict(list)
    pol_by_lub = defaultdict(list)
    if lubs:
        variants: list[str] = []
        for lub in lubs:
            for v in lub_match_variants(lub):
                if v not in variants:
                    variants.append(v)
        placeholders = ",".join("?" * len(variants))
        t = tuple(variants)
        seen_per_lub: dict = defaultdict(set)
        # 1. Farby z polem lub= (klasyczne przypisanie) — także w_uzyciu / dostępne
        cur.execute("SELECT * FROM farby WHERE lub IN (%s)" % placeholders, t)
        for f in cur.fetchall():
            fd = dict(f)
            fd["mag_alert"] = alert_daty(f["data_produkcji"]) if f["data_produkcji"] else "ok"
            key = normalize_lub_key(f["lub"])
            if key:
                farby_by_lub[key].append(fd)
                seen_per_lub[key].add(f["id"])
        # 2. Farby z tabeli farba_lub_assignments (ręczne / wielokrotne)
        try:
            cur.execute(
                "SELECT f.*, fla.lub_number AS assigned_lub FROM farby f "
                "JOIN farba_lub_assignments fla ON f.id = fla.farba_id "
                "WHERE fla.lub_number IN (%s)" % placeholders,
                t,
            )
            for f in cur.fetchall():
                fd = dict(f)
                assigned_lub = fd.pop("assigned_lub", None)
                key = normalize_lub_key(assigned_lub)
                if not key:
                    continue
                fd["mag_alert"] = alert_daty(fd.get("data_produkcji")) if fd.get("data_produkcji") else "ok"
                if fd["id"] not in seen_per_lub.get(key, set()):
                    farby_by_lub[key].append(fd)
                    seen_per_lub[key].add(fd["id"])
        except Exception:
            pass  # tabela może nie istnieć na starszych bazach
        # 3. Polimery
        cur.execute("SELECT * FROM polymers WHERE lub IN (%s)" % placeholders, t)
        for po in cur.fetchall():
            key = normalize_lub_key(po["lub"])
            if key:
                pol_by_lub[key].append(dict(po))
    out = []
    for p in plan_rows:
        d = dict(p)
        lub_key = normalize_lub_key(d.get("lub_number"))
        d["farby"] = farby_by_lub.get(lub_key, []) if lub_key else []
        d["polimery"] = pol_by_lub.get(lub_key, []) if lub_key else []
        st = d.get("assortment_prep_status")
        if not st:
            d["assortment_prep_status"] = "pending"
        if not d.get("farby_prep_status"):
            d["farby_prep_status"] = "pending"
        if not d.get("polimery_prep_status"):
            d["polimery_prep_status"] = "pending"
        out.append(d)
    return out


def get_lub_farby(cur, lub_number: str) -> list:
    """Zwraca wszystkie farby powiązane z danym numerem LUB (bezpośrednio i przez assignment)."""
    variants = lub_match_variants(lub_number)
    if not variants:
        return []
    result = []
    seen_ids: set = set()
    placeholders = ",".join("?" * len(variants))
    cur.execute(f"SELECT * FROM farby WHERE lub IN ({placeholders})", tuple(variants))
    for f in cur.fetchall():
        fd = dict(f)
        fd["mag_alert"] = alert_daty(fd.get("data_produkcji")) if fd.get("data_produkcji") else "ok"
        result.append(fd)
        seen_ids.add(fd["id"])
    try:
        cur.execute(
            f"SELECT f.* FROM farby f JOIN farba_lub_assignments fla ON f.id=fla.farba_id "
            f"WHERE fla.lub_number IN ({placeholders})",
            tuple(variants),
        )
        for f in cur.fetchall():
            if f["id"] not in seen_ids:
                fd = dict(f)
                fd["mag_alert"] = alert_daty(fd.get("data_produkcji")) if fd.get("data_produkcji") else "ok"
                result.append(fd)
                seen_ids.add(fd["id"])
    except Exception:
        pass
    return result


def get_lub_polimery(cur, lub_number: str) -> list:
    """Polimery/matryce dla LUB — z normalizacją zapisu numeru."""
    variants = lub_match_variants(lub_number)
    if not variants:
        return []
    placeholders = ",".join("?" * len(variants))
    cur.execute(f"SELECT * FROM polymers WHERE lub IN ({placeholders})", tuple(variants))
    return [dict(p) for p in cur.fetchall()]
