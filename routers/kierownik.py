"""
Router: panel kierownika — raporty, statystyki, dziennik zmian.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from dependencies import get_db, require_manager_or_admin
from helpers import (
    PRODUCTION_MACHINES,
    WINDING_MACHINES,
    normalize_shift_label,
    render_template,
)
from services.kierownik_reports import (
    build_daily_packages,
    build_month_history,
    build_stats,
    render_shift_summary_pdf,
)
from time_utils import local_day_bounds_utc, local_today, utc_threshold_db_string

router = APIRouter(prefix="/kierownik")


STATUS_LABELS = {
    "run": "Run",
    "przezbrojenie": "Przezbrojenie",
    "problem": "Problem",
    "nieobsadzona": "Nieobsadzona",
    "operator": "Operator",
}


def _machine_operator_name(cur, machine: str, plan_id=None, since_utc=None):
    if plan_id:
        cur.execute(
            """
            SELECT actor_user FROM events
            WHERE event_type='JOB_STARTED' AND machine=? AND plan_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (machine, plan_id),
        )
        row = cur.fetchone()
        if row and row["actor_user"]:
            return row["actor_user"]
    if since_utc:
        cur.execute(
            """
            SELECT actor_user FROM events
            WHERE event_type='JOB_STARTED' AND machine=? AND created_at >= ?
            ORDER BY id DESC LIMIT 1
            """,
            (machine, since_utc),
        )
    else:
        cur.execute(
            """
            SELECT actor_user FROM events
            WHERE event_type='JOB_STARTED' AND machine=?
            ORDER BY id DESC LIMIT 1
            """,
            (machine,),
        )
    row = cur.fetchone()
    return row["actor_user"] if row and row["actor_user"] else None


def _open_machine_problem(cur, machine: str):
    cur.execute(
        """
        SELECT pc.label, pri.short_note
        FROM production_report_issues pri
        JOIN production_reports pr ON pr.id = pri.production_report_id
        JOIN problem_categories pc ON pc.id = pri.problem_category_id
        WHERE pr.machine=? AND pri.status='new'
        ORDER BY pri.id DESC LIMIT 1
        """,
        (machine,),
    )
    row = cur.fetchone()
    if row:
        return row["short_note"] or row["label"]
    return None


@router.get("")
def kierownik(request: Request, user=Depends(require_manager_or_admin), conn=Depends(get_db)):
    cur = conn.cursor()
    start_utc, _end_utc = local_day_bounds_utc()
    machine_rows = []

    for machine in list(PRODUCTION_MACHINES) + list(WINDING_MACHINES):
        is_winder = machine in WINDING_MACHINES
        machine_type = "przewijarka" if is_winder else "druk"
        href = f"/przewijarka/{machine}/plany" if is_winder else f"/maszyna/{machine}/plany"

        cur.execute(
            """
            SELECT id, order_number, order_name, status, lub_number
            FROM production_plans
            WHERE machine=? AND status='in_progress'
            ORDER BY id DESC LIMIT 1
            """,
            (machine,),
        )
        running_plan = cur.fetchone()
        cur.execute(
            """
            SELECT id, order_number, order_name, status, lub_number
            FROM production_plans
            WHERE machine=? AND status='planned'
            ORDER BY id ASC LIMIT 1
            """,
            (machine,),
        )
        next_plan = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) AS pending_count FROM production_plans WHERE machine=? AND status='planned'",
            (machine,),
        )
        pending_count = cur.fetchone()["pending_count"] or 0

        problem_note = _open_machine_problem(cur, machine)
        display_plan = running_plan or next_plan
        operator_name = None

        if problem_note:
            status_key = "problem"
            operator_name = _machine_operator_name(
                cur, machine, running_plan["id"] if running_plan else None
            )
        elif running_plan:
            status_key = "run"
            operator_name = _machine_operator_name(cur, machine, running_plan["id"])
        elif pending_count > 0:
            status_key = "przezbrojenie"
            operator_name = _machine_operator_name(cur, machine, since_utc=start_utc)
        else:
            operator_name = _machine_operator_name(cur, machine, since_utc=start_utc)
            status_key = "operator" if operator_name else "nieobsadzona"

        machine_rows.append({
            "machine": machine,
            "machine_type": machine_type,
            "href": href,
            "status_key": status_key,
            "status_label": STATUS_LABELS[status_key],
            "active_plan": dict(display_plan) if display_plan else None,
            "pending_count": pending_count,
            "operator_name": operator_name,
            "problem_note": problem_note,
        })

    return render_template("kierownik.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "machine_rows": machine_rows,
    })


@router.get("/raporty")
def kierownik_raporty_hub(request: Request, user=Depends(require_manager_or_admin)):
    return RedirectResponse("/kierownik/raporty-dzienne", status_code=303)


@router.get("/raporty-dzienne")
def kierownik_raporty_dzienne(
    request: Request,
    user=Depends(require_manager_or_admin),
    date_q: str = Query("", alias="date"),
    zmiana: str = Query(""),
    conn=Depends(get_db),
):
    if not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    shift = normalize_shift_label(zmiana) if zmiana else None
    if shift not in ("dzien", "noc"):
        shift = None
    cur = conn.cursor()
    payload = build_daily_packages(cur, date_q, shift=shift)
    return render_template("kierownik_raporty_dzienne.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "date_q": payload["date_q"],
        "zmiana": shift or "",
        "packages": payload["packages"],
        "totals": payload["totals"],
    })


@router.get("/raporty-dzienne/pdf")
def kierownik_raporty_dzienne_pdf(
    request: Request,
    user=Depends(require_manager_or_admin),
    date_q: str = Query("", alias="date"),
    zmiana: str = Query("dzien"),
    conn=Depends(get_db),
):
    if not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    shift = normalize_shift_label(zmiana) if zmiana else "dzien"
    if shift not in ("dzien", "noc"):
        shift = "dzien"
    cur = conn.cursor()
    payload = build_daily_packages(cur, date_q, shift=shift)
    pdf_bytes = render_shift_summary_pdf(payload, generated_by=user["username"])
    filename = f"raport_zmiany_{date_q}_{shift}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/historia-raportow")
def kierownik_historia_raportow(
    request: Request,
    user=Depends(require_manager_or_admin),
    year: int | None = Query(None),
    month: int | None = Query(None),
    conn=Depends(get_db),
):
    today = local_today()
    year = int(year or today.year)
    month = int(month or today.month)
    if month < 1 or month > 12:
        month = today.month
    cur = conn.cursor()
    history = build_month_history(cur, year, month)
    return render_template("kierownik_historia_raportow.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "year": history["year"],
        "month": history["month"],
        "month_label": history["month_label"],
        "days": history["days"],
        "active_days": history["active_days"],
        "totals": history["totals"],
    })


@router.get("/statystyki")
def kierownik_statystyki(
    request: Request,
    user=Depends(require_manager_or_admin),
    tryb: str = Query("daily"),
    date_q: str = Query("", alias="date"),
    year: int | None = Query(None),
    month: int | None = Query(None),
    conn=Depends(get_db),
):
    mode = "monthly" if (tryb or "").lower() in ("monthly", "miesiac", "miesiąc", "month") else "daily"
    if mode == "daily" and not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    cur = conn.cursor()
    stats = build_stats(cur, mode=mode, date_q=date_q or None, year=year, month=month)
    return render_template("kierownik_statystyki.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "mode": stats["mode"],
        "period_label": stats["period_label"],
        "date_q": stats["date_q"] or date_q,
        "year": stats["year"],
        "month": stats["month"],
        "shifts": stats["shifts"],
        "days": stats["days"],
        "totals": stats["totals"],
    })


@router.get("/rejestr-raportow")
def kierownik_rejestr_raportow(
    request: Request,
    user=Depends(require_manager_or_admin),
    date_q: str = Query("", alias="date"),
):
    if not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    return RedirectResponse(f"/kierownik/raporty-dzienne?date={date_q}", status_code=303)


@router.get("/raport-zmiany")
def kierownik_raport_zmiany(
    request: Request,
    user=Depends(require_manager_or_admin),
    date_q: str = Query("", alias="date"),
    zmiana: str = Query("dzien"),
):
    if not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    zm = normalize_shift_label(zmiana)
    return RedirectResponse(f"/kierownik/raporty-dzienne?date={date_q}&zmiana={zm}", status_code=303)


@router.get("/raport-dziennie")
def kierownik_raport_dziennie(request: Request, user=Depends(require_manager_or_admin)):
    return RedirectResponse("/kierownik/raporty-dzienne", status_code=303)


@router.get("/statystyki-zmian")
def kierownik_statystyki_zmian(
    request: Request,
    user=Depends(require_manager_or_admin),
    date_q: str = Query("", alias="date"),
):
    if not date_q:
        date_q = local_today().strftime("%Y-%m-%d")
    return RedirectResponse(f"/kierownik/statystyki?tryb=daily&date={date_q}", status_code=303)


@router.get("/raport-jakosci")
def kierownik_raport_jakosci(request: Request, user=Depends(require_manager_or_admin)):
    return RedirectResponse("/kierownik/raporty-dzienne", status_code=303)


@router.get("/dziennik-zmian")
def kierownik_dziennik_zmian(request: Request, user=Depends(require_manager_or_admin), conn=Depends(get_db)):
    cur = conn.cursor()
    cur.execute("""
        SELECT pl.*, p.order_number, p.machine
        FROM production_log pl
        LEFT JOIN production_plans p ON pl.plan_id = p.id
        WHERE pl.created_at >= ?
        ORDER BY pl.created_at DESC
    """, (utc_threshold_db_string(days=7),))
    operations = cur.fetchall()
    return render_template("kierownik_dziennik_zmian.html", {
        "operations": operations,
        "user": {"username": user["username"], "role": user["role"]},
    })
