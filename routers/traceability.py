from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from dependencies import get_db, require_auth
from helpers import get_lub_farby, lub_match_variants, normalize_lub_key, render_template

router = APIRouter()


def _can_view_traceability(user: dict) -> bool:
    return user.get("role") in ("admin", "manager", "kierownik")


def _as_dict(row, cur=None) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    if cur is not None and cur.description:
        return dict(zip([desc[0] for desc in cur.description], row))
    return {}


def _search_candidates(query: str) -> list[str]:
    """Warianty zapytania: surowe, bez .0, oraz formy LUB."""
    raw = (query or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for item in (raw, raw.upper(), raw.lower()):
        if item and item not in out:
            out.append(item)
    if raw.endswith(".0") and raw[:-2].replace(".", "", 1).isdigit():
        trimmed = raw[:-2]
        if trimmed not in out:
            out.append(trimmed)
    for v in lub_match_variants(raw):
        if v not in out:
            out.append(v)
    return out


def _find_plan(cur, query_norm: str) -> dict | None:
    candidates = _search_candidates(query_norm)
    if not candidates:
        return None

    placeholders = ",".join("?" * len(candidates))
    params = tuple(candidates) * 3
    cur.execute(
        f"""
        SELECT * FROM production_plans
        WHERE order_number IN ({placeholders})
           OR artwork_number IN ({placeholders})
           OR lub_number IN ({placeholders})
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    )
    row = cur.fetchone()
    if row:
        return _as_dict(row, cur)

    # Fallback: znormalizowany LUB (LUB4475 ↔ 4475) gdy dokładne IN nie złapało
    key = normalize_lub_key(query_norm)
    if key:
        cur.execute(
            """
            SELECT * FROM production_plans
            WHERE lub_number IS NOT NULL AND TRIM(lub_number) != ''
            ORDER BY id DESC
            LIMIT 300
            """
        )
        for plan_row in cur.fetchall():
            plan = _as_dict(plan_row, cur)
            if normalize_lub_key(plan.get("lub_number")) == key:
                return plan

    # Fallback: artwork bez spacji / z .0
    art_key = query_norm.replace(" ", "").rstrip(".0") if query_norm.replace(".", "", 1).isdigit() or "." in query_norm else query_norm.replace(" ", "")
    if art_key:
        cur.execute(
            """
            SELECT * FROM production_plans
            WHERE artwork_number IS NOT NULL AND TRIM(artwork_number) != ''
            ORDER BY id DESC
            LIMIT 300
            """
        )
        for plan_row in cur.fetchall():
            plan = _as_dict(plan_row, cur)
            stored = str(plan.get("artwork_number") or "").strip().replace(" ", "")
            if stored == query_norm or stored == art_key:
                return plan
            if stored.endswith(".0") and stored[:-2] == art_key:
                return plan

    return None


def _build_traceability_context(cur, query: str) -> dict:
    query_norm = (query or "").strip()
    plan = _find_plan(cur, query_norm) if query_norm else None

    materials = []
    reports = []
    print_reports = []
    winding_reports = []
    events = []

    if plan:
        plan_id = plan["id"]
        cur.execute(
            "SELECT * FROM production_reports WHERE plan_id=? ORDER BY created_at DESC",
            (plan_id,),
        )
        reports = [_as_dict(row, cur) for row in cur.fetchall()]

        cur.execute(
            "SELECT * FROM print_control_reports WHERE plan_id=? ORDER BY created_at DESC",
            (plan_id,),
        )
        print_reports = [_as_dict(row, cur) for row in cur.fetchall()]

        cur.execute(
            "SELECT * FROM winding_reports WHERE plan_id=? ORDER BY created_at DESC",
            (plan_id,),
        )
        winding_reports = [_as_dict(row, cur) for row in cur.fetchall()]

        lub_number = plan.get("lub_number")
        if lub_number:
            materials = get_lub_farby(cur, lub_number)

        cur.execute(
            "SELECT * FROM events WHERE plan_id=? ORDER BY created_at DESC",
            (plan_id,),
        )
        events = [_as_dict(row, cur) for row in cur.fetchall()]

    return {
        "query": query_norm,
        "plan": plan,
        "materials": materials,
        "reports": reports,
        "print_reports": print_reports,
        "winding_reports": winding_reports,
        "events": events,
    }


@router.get("/traceability")
def traceability(
    request: Request,
    query: str = Query(""),
    user=Depends(require_auth),
    conn=Depends(get_db),
):
    if not _can_view_traceability(user):
        return RedirectResponse("/dashboard", status_code=303)

    cur = conn.cursor()
    context = _build_traceability_context(cur, query)

    return render_template("traceability.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "context": context,
    })
