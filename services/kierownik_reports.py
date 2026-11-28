"""Agregacja raportów kierownika: pakiety dzienne, historia miesiąca, statystyki, PDF zmiany."""

from __future__ import annotations

import calendar
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from helpers import _parse_shift_time, normalize_shift_label

DEJAVU_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _as_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def shift_key_for_print(report_date: str, report_time, ref_day: date) -> str | None:
    """Przypisuje kontrolę zadruku do zmiany dnia referencyjnego (6–18 / 18–6)."""
    time_parts = _parse_shift_time(report_time)
    hour = time_parts[0] if time_parts else 12
    row_date = str(report_date or "")[:10]
    ref = ref_day.strftime("%Y-%m-%d")
    nxt = (ref_day + timedelta(days=1)).strftime("%Y-%m-%d")
    if row_date == ref and 6 <= hour < 18:
        return "dzien"
    if row_date == ref and hour >= 18:
        return "noc"
    if row_date == nxt and hour < 6:
        return "noc"
    return None


def _package_key(plan_id, order_number: str, machine: str) -> str:
    if plan_id:
        return f"plan:{plan_id}"
    return f"job:{order_number}|{machine}"


def build_daily_packages(cur, date_q: str, shift: str | None = None) -> dict:
    """
    Pakiety zleceń na dzień: raporty końcowe + kontrole zadruku przypięte do numeru zlecenia.
    shift: None = cały dzień, 'dzien' / 'noc'.
    """
    try:
        ref_day = datetime.strptime(date_q, "%Y-%m-%d").date()
    except ValueError:
        ref_day = date.today()
        date_q = ref_day.strftime("%Y-%m-%d")

    shift_norm = normalize_shift_label(shift) if shift else None
    if shift_norm not in (None, "dzien", "noc"):
        shift_norm = None

    packages: dict[str, dict] = {}

    def _ensure(plan_id, order_number: str, machine: str) -> dict:
        key = _package_key(plan_id, order_number, machine)
        if key not in packages:
            packages[key] = {
                "key": key,
                "plan_id": plan_id,
                "order_number": order_number or "—",
                "machine": machine or "—",
                "order_name": None,
                "lub_number": None,
                "artwork_number": None,
                "production_reports": [],
                "print_reports": [],
                "qty": 0,
                "ok": 0,
                "nok": 0,
                "roll_count": 0,
                "roll_meters": 0.0,
                "roll_pieces": 0,
                "print_ok": 0,
                "print_nok": 0,
            }
        return packages[key]

    cur.execute(
        """
        SELECT pr.*, pp.order_name, pp.lub_number, pp.artwork_number, pp.order_number AS plan_order
        FROM production_reports pr
        LEFT JOIN production_plans pp ON pp.id = pr.plan_id
        WHERE pr.date=?
        ORDER BY pr.machine, pr.created_at, pr.id
        """,
        (date_q,),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        if shift_norm and normalize_shift_label(r.get("shift")) != shift_norm:
            continue
        order = r.get("plan_order") or r.get("job_number") or "—"
        pkg = _ensure(r.get("plan_id"), order, r.get("machine") or "—")
        if r.get("order_name"):
            pkg["order_name"] = r["order_name"]
        if r.get("lub_number"):
            pkg["lub_number"] = r["lub_number"]
        if r.get("artwork_number"):
            pkg["artwork_number"] = r["artwork_number"]
        pkg["production_reports"].append(r)
        pkg["qty"] += int(r.get("quantity") or 0)
        pkg["ok"] += int(r.get("ok_quantity") or 0)
        pkg["nok"] += int(r.get("nok_quantity") or 0)

    next_day = (ref_day + timedelta(days=1)).strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT pcr.*, pp.order_name, pp.lub_number, pp.artwork_number, pp.order_number AS plan_order
        FROM print_control_reports pcr
        LEFT JOIN production_plans pp ON pp.id = pcr.plan_id
        WHERE pcr.date=? OR pcr.date=?
        ORDER BY pcr.machine, pcr.created_at, pcr.id
        """,
        (date_q, next_day),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        sk = shift_key_for_print(r.get("date"), r.get("time"), ref_day)
        if sk is None:
            continue
        if shift_norm and sk != shift_norm:
            continue
        order = r.get("plan_order") or r.get("job_number") or "—"
        pkg = _ensure(r.get("plan_id"), order, r.get("machine") or "—")
        if r.get("order_name"):
            pkg["order_name"] = r["order_name"]
        if r.get("lub_number"):
            pkg["lub_number"] = r["lub_number"]
        if r.get("artwork_number"):
            pkg["artwork_number"] = r["artwork_number"]
        pkg["print_reports"].append(r)
        pkg["roll_count"] += 1
        pkg["roll_meters"] += float(r.get("roll_meters") or 0)
        pkg["roll_pieces"] += int(r.get("roll_pieces") or 0)
        if (r.get("status") or "").upper() == "OK":
            pkg["print_ok"] += 1
        else:
            pkg["print_nok"] += 1

    # Uzupełnij meta planu, jeśli pakiet powstał tylko z kontroli
    missing_meta = [p for p in packages.values() if p.get("plan_id") and not p.get("order_name")]
    for pkg in missing_meta:
        cur.execute(
            "SELECT order_name, lub_number, artwork_number, order_number FROM production_plans WHERE id=?",
            (pkg["plan_id"],),
        )
        plan = cur.fetchone()
        if not plan:
            continue
        plan = _as_dict(plan)
        pkg["order_name"] = plan.get("order_name")
        pkg["lub_number"] = plan.get("lub_number")
        pkg["artwork_number"] = plan.get("artwork_number")
        if plan.get("order_number"):
            pkg["order_number"] = plan["order_number"]

    ordered = sorted(
        packages.values(),
        key=lambda p: (str(p.get("machine") or ""), str(p.get("order_number") or ""), int(p.get("plan_id") or 0)),
    )

    totals = {
        "packages": len(ordered),
        "qty": sum(p["qty"] for p in ordered),
        "ok": sum(p["ok"] for p in ordered),
        "nok": sum(p["nok"] for p in ordered),
        "roll_count": sum(p["roll_count"] for p in ordered),
        "roll_meters": round(sum(p["roll_meters"] for p in ordered), 2),
        "roll_pieces": sum(p["roll_pieces"] for p in ordered),
        "print_ok": sum(p["print_ok"] for p in ordered),
        "print_nok": sum(p["print_nok"] for p in ordered),
        "final_reports": sum(len(p["production_reports"]) for p in ordered),
    }
    total_out = totals["ok"] + totals["nok"]
    totals["efficiency"] = round(totals["ok"] * 100 / total_out, 1) if total_out else 0.0
    totals["waste_pct"] = round(totals["nok"] * 100 / total_out, 1) if total_out else 0.0

    return {
        "date_q": date_q,
        "shift": shift_norm,
        "packages": ordered,
        "totals": totals,
    }


def build_month_history(cur, year: int, month: int) -> dict:
    """Wykaz dni miesiąca z liczbą pakietów / raportów."""
    year = int(year)
    month = int(month)
    days_in_month = calendar.monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end_day = f"{year:04d}-{month:02d}-{days_in_month:02d}"

    by_day: dict[str, dict] = {}
    for d in range(1, days_in_month + 1):
        key = f"{year:04d}-{month:02d}-{d:02d}"
        by_day[key] = {
            "date": key,
            "final_reports": 0,
            "print_reports": 0,
            "packages_estimate": 0,
            "qty": 0,
            "ok": 0,
            "nok": 0,
        }

    cur.execute(
        """
        SELECT date,
               COUNT(*) AS cnt,
               COUNT(DISTINCT COALESCE(plan_id, -id)) AS packages,
               COALESCE(SUM(quantity), 0) AS qty,
               COALESCE(SUM(ok_quantity), 0) AS ok,
               COALESCE(SUM(nok_quantity), 0) AS nok
        FROM production_reports
        WHERE date >= ? AND date <= ?
        GROUP BY date
        """,
        (start, end_day),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        key = str(r.get("date") or "")[:10]
        if key not in by_day:
            continue
        by_day[key]["final_reports"] = int(r.get("cnt") or 0)
        by_day[key]["packages_estimate"] = int(r.get("packages") or 0)
        by_day[key]["qty"] = int(r.get("qty") or 0)
        by_day[key]["ok"] = int(r.get("ok") or 0)
        by_day[key]["nok"] = int(r.get("nok") or 0)

    cur.execute(
        """
        SELECT date, COUNT(*) AS cnt
        FROM print_control_reports
        WHERE date >= ? AND date <= ?
        GROUP BY date
        """,
        (start, end_day),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        key = str(r.get("date") or "")[:10]
        if key not in by_day:
            continue
        by_day[key]["print_reports"] = int(r.get("cnt") or 0)
        # jeśli nie było finali, szacuj pakiety z kontroli
        if by_day[key]["packages_estimate"] == 0 and by_day[key]["print_reports"]:
            by_day[key]["packages_estimate"] = by_day[key]["print_reports"]

    days = list(by_day.values())
    active_days = [d for d in days if d["final_reports"] or d["print_reports"]]
    month_totals = {
        "final_reports": sum(d["final_reports"] for d in days),
        "print_reports": sum(d["print_reports"] for d in days),
        "qty": sum(d["qty"] for d in days),
        "ok": sum(d["ok"] for d in days),
        "nok": sum(d["nok"] for d in days),
        "active_days": len(active_days),
    }
    return {
        "year": year,
        "month": month,
        "month_label": f"{year:04d}-{month:02d}",
        "days": days,
        "active_days": active_days,
        "totals": month_totals,
    }


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def _job_durations_hours(cur, start_date: str, end_date: str) -> list[float]:
    """Średnia długość zlecenia z JOB_STARTED → JOB_COMPLETED (w godzinach)."""
    cur.execute(
        """
        SELECT plan_id, event_type, created_at
        FROM events
        WHERE event_type IN ('JOB_STARTED', 'JOB_COMPLETED')
          AND plan_id IS NOT NULL
          AND date(created_at) >= date(?)
          AND date(created_at) <= date(?)
        ORDER BY plan_id, created_at, id
        """,
        (start_date, end_date),
    )
    starts: dict[int, datetime] = {}
    durations: list[float] = []
    for row in cur.fetchall():
        r = _as_dict(row)
        pid = int(r.get("plan_id") or 0)
        ts = _parse_ts(r.get("created_at"))
        if not pid or not ts:
            continue
        if r.get("event_type") == "JOB_STARTED":
            starts[pid] = ts
        elif r.get("event_type") == "JOB_COMPLETED" and pid in starts:
            delta = (ts - starts[pid]).total_seconds() / 3600.0
            if 0 < delta < 72:
                durations.append(delta)
            starts.pop(pid, None)
    return durations


def build_stats(cur, mode: str, date_q: str | None = None, year: int | None = None, month: int | None = None) -> dict:
    """
    Statystyki: wydajność, odpad, ilość prac/zmiana, średnia długość zleceń.
    mode: 'daily' | 'monthly'
    """
    mode = (mode or "daily").lower()
    if mode == "monthly":
        today = date.today()
        year = int(year or today.year)
        month = int(month or today.month)
        days_in_month = calendar.monthrange(year, month)[1]
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month:02d}-{days_in_month:02d}"
        period_label = f"{year:04d}-{month:02d}"
    else:
        if not date_q:
            date_q = date.today().strftime("%Y-%m-%d")
        start = end = date_q
        period_label = date_q
        try:
            year = datetime.strptime(date_q, "%Y-%m-%d").year
            month = datetime.strptime(date_q, "%Y-%m-%d").month
        except ValueError:
            year = date.today().year
            month = date.today().month

    shifts = {
        "dzien": {"key": "dzien", "label": "Dzień", "jobs": 0, "qty": 0, "ok": 0, "nok": 0, "rolls": 0},
        "noc": {"key": "noc", "label": "Noc", "jobs": 0, "qty": 0, "ok": 0, "nok": 0, "rolls": 0},
    }
    jobs_seen = {"dzien": set(), "noc": set()}
    by_day: dict[str, dict] = defaultdict(lambda: {
        "date": "",
        "qty": 0,
        "ok": 0,
        "nok": 0,
        "jobs": 0,
        "rolls": 0,
    })
    day_jobs: dict[str, set] = defaultdict(set)

    cur.execute(
        """
        SELECT date, shift, machine, plan_id, job_number,
               COALESCE(quantity, 0) AS quantity,
               COALESCE(ok_quantity, 0) AS ok_quantity,
               COALESCE(nok_quantity, 0) AS nok_quantity
        FROM production_reports
        WHERE date >= ? AND date <= ?
        """,
        (start, end),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        sk = normalize_shift_label(r.get("shift"))
        if sk not in shifts:
            continue
        day = str(r.get("date") or "")[:10]
        qty = int(r.get("quantity") or 0)
        ok = int(r.get("ok_quantity") or 0)
        nok = int(r.get("nok_quantity") or 0)
        shifts[sk]["qty"] += qty
        shifts[sk]["ok"] += ok
        shifts[sk]["nok"] += nok
        job_id = r.get("plan_id") or f"{r.get('job_number')}|{r.get('machine')}"
        jobs_seen[sk].add(job_id)
        by_day[day]["date"] = day
        by_day[day]["qty"] += qty
        by_day[day]["ok"] += ok
        by_day[day]["nok"] += nok
        day_jobs[day].add(job_id)

    # Kontrole zadruku → rolek / shift
    try:
        end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        end_dt = date.today()
    cur.execute(
        """
        SELECT date, time, plan_id, job_number, machine, status
        FROM print_control_reports
        WHERE date >= ? AND date <= ?
        """,
        (start, (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")),
    )
    for row in cur.fetchall():
        r = _as_dict(row)
        row_day = str(r.get("date") or "")[:10]
        try:
            day_ref = datetime.strptime(row_day, "%Y-%m-%d").date()
        except ValueError:
            continue
        # noc po północy: raport z next day hour<6 należy do poprzedniego dnia roboczego
        time_parts = _parse_shift_time(r.get("time"))
        hour = time_parts[0] if time_parts else 12
        if hour < 6:
            work_day = (day_ref - timedelta(days=1)).strftime("%Y-%m-%d")
            sk = "noc"
        elif hour >= 18:
            work_day = row_day
            sk = "noc"
        else:
            work_day = row_day
            sk = "dzien"
        if work_day < start or work_day > end:
            continue
        if sk in shifts:
            shifts[sk]["rolls"] += 1
        by_day[work_day]["date"] = work_day
        by_day[work_day]["rolls"] += 1

    for sk in shifts:
        shifts[sk]["jobs"] = len(jobs_seen[sk])
        out = shifts[sk]["ok"] + shifts[sk]["nok"]
        shifts[sk]["efficiency"] = round(shifts[sk]["ok"] * 100 / out, 1) if out else 0.0
        shifts[sk]["waste_pct"] = round(shifts[sk]["nok"] * 100 / out, 1) if out else 0.0

    for day, jobs in day_jobs.items():
        by_day[day]["jobs"] = len(jobs)
        out = by_day[day]["ok"] + by_day[day]["nok"]
        by_day[day]["efficiency"] = round(by_day[day]["ok"] * 100 / out, 1) if out else 0.0
        by_day[day]["waste_pct"] = round(by_day[day]["nok"] * 100 / out, 1) if out else 0.0

    durations = _job_durations_hours(cur, start, end)
    avg_hours = round(sum(durations) / len(durations), 2) if durations else 0.0

    total_ok = shifts["dzien"]["ok"] + shifts["noc"]["ok"]
    total_nok = shifts["dzien"]["nok"] + shifts["noc"]["nok"]
    total_out = total_ok + total_nok
    total_jobs = len(jobs_seen["dzien"] | jobs_seen["noc"])

    return {
        "mode": mode,
        "period_label": period_label,
        "date_q": date_q if mode == "daily" else None,
        "year": year,
        "month": month,
        "shifts": [shifts["dzien"], shifts["noc"]],
        "days": sorted(by_day.values(), key=lambda d: d["date"]),
        "totals": {
            "jobs": total_jobs,
            "jobs_day_shift": shifts["dzien"]["jobs"],
            "jobs_night_shift": shifts["noc"]["jobs"],
            "qty": shifts["dzien"]["qty"] + shifts["noc"]["qty"],
            "ok": total_ok,
            "nok": total_nok,
            "efficiency": round(total_ok * 100 / total_out, 1) if total_out else 0.0,
            "waste_pct": round(total_nok * 100 / total_out, 1) if total_out else 0.0,
            "rolls": shifts["dzien"]["rolls"] + shifts["noc"]["rolls"],
            "avg_job_hours": avg_hours,
            "completed_jobs_timed": len(durations),
        },
    }


def render_shift_summary_pdf(payload: dict, generated_by: str = "") -> bytes:
    """Zbiorczy raport zmiany w PDF (PyMuPDF + DejaVu)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    font = "helv"
    font_bold = "helv"
    if DEJAVU_REGULAR.exists():
        font = "dejavu"
        page.insert_font(fontname=font, fontfile=str(DEJAVU_REGULAR))
    if DEJAVU_BOLD.exists():
        font_bold = "dejavub"
        page.insert_font(fontname=font_bold, fontfile=str(DEJAVU_BOLD))

    date_q = payload.get("date_q") or "—"
    shift = payload.get("shift") or "all"
    shift_label = {"dzien": "dzień (6:00–18:00)", "noc": "noc (18:00–6:00)", "all": "cały dzień"}.get(
        shift, str(shift)
    )
    totals = payload.get("totals") or {}
    packages = payload.get("packages") or []

    y = 40
    left = 40

    def write(text: str, size=11, bold=False, color=(0, 0, 0)):
        nonlocal y, page
        if y > 800:
            page = doc.new_page(width=595, height=842)
            if DEJAVU_REGULAR.exists():
                page.insert_font(fontname=font, fontfile=str(DEJAVU_REGULAR))
            if DEJAVU_BOLD.exists():
                page.insert_font(fontname=font_bold, fontfile=str(DEJAVU_BOLD))
            y = 40
        page.insert_text(
            (left, y),
            text,
            fontsize=size,
            fontname=font_bold if bold else font,
            color=color,
        )
        y += size + 6

    write("Raport zbiorczy zmiany", size=16, bold=True)
    write(f"Data: {date_q}  ·  Zakres: {shift_label}", size=11)
    if generated_by:
        write(f"Wygenerował: {generated_by}", size=9, color=(0.3, 0.3, 0.3))
    write("")
    write("Podsumowanie", size=13, bold=True)
    write(
        f"Pakiety zleceń: {totals.get('packages', 0)}  ·  "
        f"Raporty końcowe: {totals.get('final_reports', 0)}  ·  "
        f"Kontrole zadruku: {totals.get('roll_count', 0)}"
    )
    write(
        f"Produkcja: qty {totals.get('qty', 0)}  OK {totals.get('ok', 0)}  NOK {totals.get('nok', 0)}  "
        f"wydajność {totals.get('efficiency', 0)}%  odpad {totals.get('waste_pct', 0)}%"
    )
    write(
        f"Zadruk: {totals.get('roll_meters', 0)} m / {totals.get('roll_pieces', 0)} szt.  "
        f"(OK {totals.get('print_ok', 0)} / NOK {totals.get('print_nok', 0)})"
    )
    write("")
    write("Pakiety zleceń", size=13, bold=True)

    for pkg in packages:
        write(
            f"{pkg.get('machine')} · {pkg.get('order_number')} · {pkg.get('order_name') or '—'}",
            size=10,
            bold=True,
        )
        write(
            f"  LUB {pkg.get('lub_number') or '—'}  ·  artwork {pkg.get('artwork_number') or '—'}  ·  "
            f"finali {len(pkg.get('production_reports') or [])}  ·  rolek {pkg.get('roll_count', 0)}",
            size=9,
        )
        write(
            f"  qty {pkg.get('qty', 0)}  OK {pkg.get('ok', 0)}  NOK {pkg.get('nok', 0)}  ·  "
            f"zadruk {pkg.get('roll_meters', 0)} m / {pkg.get('roll_pieces', 0)} szt.",
            size=9,
        )
        y += 2

    if not packages:
        write("Brak pakietów raportów w wybranym zakresie.", size=10, color=(0.4, 0.4, 0.4))

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()
