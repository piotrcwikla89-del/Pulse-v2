from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from dependencies import get_db, require_auth
from helpers import (
    get_lub_farby,
    get_lub_polimery,
    lub_match_variants,
    normalize_lub_key,
    plan_production_targets,
    render_template,
    sum_print_control_rolls,
)

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


def normalize_artwork_key(value) -> str:
    text = str(value or "").strip().replace(" ", "")
    if text.endswith(".0") and text[:-2].replace(".", "", 1).isdigit():
        text = text[:-2]
    return text.upper()


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


def _detect_match_mode(query_norm: str, plans: list[dict]) -> str:
    if not plans:
        return ""
    candidates = {c.upper() for c in _search_candidates(query_norm)}
    lub_key = normalize_lub_key(query_norm)
    art_key = normalize_artwork_key(query_norm)

    order_hit = any(str(p.get("order_number") or "").strip().upper() in candidates for p in plans)
    if order_hit:
        return "order"

    lub_hit = bool(lub_key) and any(normalize_lub_key(p.get("lub_number")) == lub_key for p in plans)
    art_hit = bool(art_key) and any(normalize_artwork_key(p.get("artwork_number")) == art_key for p in plans)
    if lub_hit and not art_hit:
        return "lub"
    if art_hit and not lub_hit:
        return "artwork"
    if lub_hit and art_hit:
        return "mixed"
    return "mixed"


def _plan_matches_query(plan: dict, query_norm: str) -> bool:
    candidates = {c.upper() for c in _search_candidates(query_norm)}
    order = str(plan.get("order_number") or "").strip().upper()
    if order and order in candidates:
        return True

    lub_key = normalize_lub_key(query_norm)
    if lub_key and normalize_lub_key(plan.get("lub_number")) == lub_key:
        return True

    art_key = normalize_artwork_key(query_norm)
    if art_key and normalize_artwork_key(plan.get("artwork_number")) == art_key:
        return True

    artwork = str(plan.get("artwork_number") or "").strip().upper()
    lub = str(plan.get("lub_number") or "").strip().upper()
    return (artwork and artwork in candidates) or (lub and lub in candidates)


def _find_matching_plans(cur, query_norm: str) -> list[dict]:
    """Wszystkie historyczne plany pasujące do zlecenia / LUB / artwork."""
    if not query_norm:
        return []

    candidates = _search_candidates(query_norm)
    found: dict[int, dict] = {}

    if candidates:
        placeholders = ",".join("?" * len(candidates))
        params = tuple(candidates) * 3
        cur.execute(
            f"""
            SELECT * FROM production_plans
            WHERE order_number IN ({placeholders})
               OR artwork_number IN ({placeholders})
               OR lub_number IN ({placeholders})
            ORDER BY id DESC
            """,
            params,
        )
        for row in cur.fetchall():
            plan = _as_dict(row, cur)
            found[int(plan["id"])] = plan

    lub_key = normalize_lub_key(query_norm)
    art_key = normalize_artwork_key(query_norm)
    if lub_key or art_key:
        cur.execute(
            """
            SELECT * FROM production_plans
            WHERE (lub_number IS NOT NULL AND TRIM(lub_number) != '')
               OR (artwork_number IS NOT NULL AND TRIM(artwork_number) != '')
            ORDER BY id DESC
            LIMIT 2500
            """
        )
        for row in cur.fetchall():
            plan = _as_dict(row, cur)
            pid = int(plan["id"])
            if pid in found:
                continue
            if _plan_matches_query(plan, query_norm):
                found[pid] = plan

    return sorted(found.values(), key=lambda p: int(p.get("id") or 0), reverse=True)


def _related_plans(cur, plan: dict, exclude_ids: set[int] | None = None) -> dict:
    """Historyczne zlecenia pod tym samym LUB i/lub artwork."""
    exclude_ids = exclude_ids or set()
    by_lub: list[dict] = []
    by_artwork: list[dict] = []
    lub_key = normalize_lub_key(plan.get("lub_number"))
    art_key = normalize_artwork_key(plan.get("artwork_number"))

    if lub_key or art_key:
        cur.execute(
            """
            SELECT * FROM production_plans
            WHERE (lub_number IS NOT NULL AND TRIM(lub_number) != '')
               OR (artwork_number IS NOT NULL AND TRIM(artwork_number) != '')
            ORDER BY id DESC
            LIMIT 2500
            """
        )
        for row in cur.fetchall():
            other = _as_dict(row, cur)
            oid = int(other["id"])
            if oid in exclude_ids:
                continue
            if lub_key and normalize_lub_key(other.get("lub_number")) == lub_key:
                by_lub.append(other)
            if art_key and normalize_artwork_key(other.get("artwork_number")) == art_key:
                by_artwork.append(other)

    return {"by_lub": by_lub, "by_artwork": by_artwork}


def _fetch_for_plan_ids(cur, plan_ids: list[int]) -> dict:
    if not plan_ids:
        return {
            "reports": [],
            "print_reports": [],
            "winding_reports": [],
            "events": [],
            "production_log": [],
            "issues": [],
        }

    placeholders = ",".join("?" * len(plan_ids))
    params = tuple(plan_ids)

    cur.execute(
        f"SELECT * FROM production_reports WHERE plan_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
        params,
    )
    reports = [_as_dict(row, cur) for row in cur.fetchall()]

    cur.execute(
        f"SELECT * FROM print_control_reports WHERE plan_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
        params,
    )
    print_reports = [_as_dict(row, cur) for row in cur.fetchall()]

    cur.execute(
        f"SELECT * FROM winding_reports WHERE plan_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
        params,
    )
    winding_reports = [_as_dict(row, cur) for row in cur.fetchall()]

    cur.execute(
        f"SELECT * FROM events WHERE plan_id IN ({placeholders}) ORDER BY created_at DESC, id DESC",
        params,
    )
    events = [_as_dict(row, cur) for row in cur.fetchall()]

    production_log = []
    try:
        cur.execute(
            f'SELECT * FROM production_log WHERE plan_id IN ({placeholders}) ORDER BY created_at DESC, id DESC',
            params,
        )
        for row in cur.fetchall():
            entry = _as_dict(row, cur)
            if "username" not in entry and "user" in entry:
                entry["username"] = entry.get("user")
            production_log.append(entry)
    except Exception:
        pass

    issues = []
    try:
        cur.execute(
            f"""
            SELECT pri.*, pc.label AS category_label, pc.target_role, pr.job_number
            FROM production_report_issues pri
            LEFT JOIN problem_categories pc ON pc.id = pri.problem_category_id
            LEFT JOIN production_reports pr ON pr.id = pri.production_report_id
            WHERE pri.plan_id IN ({placeholders})
            ORDER BY pri.created_at DESC, pri.id DESC
            """,
            params,
        )
        issues = [_as_dict(row, cur) for row in cur.fetchall()]
    except Exception:
        pass

    return {
        "reports": reports,
        "print_reports": print_reports,
        "winding_reports": winding_reports,
        "events": events,
        "production_log": production_log,
        "issues": issues,
    }


def _classification_keys(query_norm: str, plans: list[dict], selected: dict | None) -> dict:
    lub_number = None
    artwork_number = None
    order_number = None

    if selected:
        lub_number = selected.get("lub_number")
        artwork_number = selected.get("artwork_number")
        order_number = selected.get("order_number")
    elif plans:
        # Wspólne klucze z wyniku wyszukiwania
        lub_keys = {normalize_lub_key(p.get("lub_number")) for p in plans if normalize_lub_key(p.get("lub_number"))}
        art_keys = {
            normalize_artwork_key(p.get("artwork_number"))
            for p in plans
            if normalize_artwork_key(p.get("artwork_number"))
        }
        if len(lub_keys) == 1:
            lub_number = next(
                (p.get("lub_number") for p in plans if normalize_lub_key(p.get("lub_number")) == next(iter(lub_keys))),
                None,
            )
        if len(art_keys) == 1:
            artwork_number = next(
                (
                    p.get("artwork_number")
                    for p in plans
                    if normalize_artwork_key(p.get("artwork_number")) == next(iter(art_keys))
                ),
                None,
            )
        if len({str(p.get("order_number") or "") for p in plans}) == 1:
            order_number = plans[0].get("order_number")

    query_lub = normalize_lub_key(query_norm)
    if query_lub and not lub_number:
        lub_number = f"LUB{query_lub}" if not str(query_norm).upper().startswith("LUB") else query_norm

    query_art = normalize_artwork_key(query_norm)
    if query_art and not artwork_number and not query_lub:
        # unikaj traktowania czystego LUB jako artwork
        if not str(query_norm).strip().upper().startswith("LUB"):
            maybe_order = any(
                str(p.get("order_number") or "").strip().upper() == query_norm.strip().upper() for p in plans
            )
            if not maybe_order:
                artwork_number = artwork_number or query_norm.strip()

    return {
        "order_number": order_number,
        "artwork_number": artwork_number,
        "lub_number": lub_number,
        "lub_key": normalize_lub_key(lub_number) if lub_number else query_lub,
        "artwork_key": normalize_artwork_key(artwork_number) if artwork_number else "",
    }


def _empty_roll_totals() -> dict:
    return {
        "roll_count": 0,
        "meters": 0.0,
        "pieces": 0,
        "ok_pieces": 0,
        "nok_pieces": 0,
        "ok_meters": 0.0,
        "nok_meters": 0.0,
    }


def _enrich_plan_summaries(cur, plans: list[dict]) -> list[dict]:
    out = []
    for plan in plans:
        item = dict(plan)
        planned = plan_production_targets(plan)
        try:
            rolls = sum_print_control_rolls(cur, int(plan["id"]))
        except Exception:
            rolls = _empty_roll_totals()
        item["planned"] = planned
        item["roll_totals"] = rolls
        planned_pieces = float(planned.get("pieces") or 0)
        planned_meters = float(planned.get("meters") or 0)
        if planned_pieces:
            item["progress_pct"] = round(float(rolls.get("pieces") or 0) * 100 / planned_pieces, 1)
        elif planned_meters:
            item["progress_pct"] = round(float(rolls.get("meters") or 0) * 100 / planned_meters, 1)
        else:
            item["progress_pct"] = 0.0
        out.append(item)
    return out


def _build_traceability_context(cur, query: str, plan_id: int | None = None) -> dict:
    query_norm = (query or "").strip()
    plans = _find_matching_plans(cur, query_norm) if query_norm else []
    match_mode = _detect_match_mode(query_norm, plans) if plans else ""

    selected = None
    if plan_id:
        for plan in plans:
            if int(plan["id"]) == int(plan_id):
                selected = plan
                break
        if selected is None:
            cur.execute("SELECT * FROM production_plans WHERE id=?", (plan_id,))
            row = cur.fetchone()
            if row:
                selected = _as_dict(row, cur)
                # jeśli użytkownik wszedł z plan_id spoza wyniku — dołącz do listy
                if selected and _plan_matches_query(selected, query_norm or str(selected.get("order_number") or "")):
                    plans = [selected] + [p for p in plans if int(p["id"]) != int(selected["id"])]
                elif selected and not query_norm:
                    plans = [selected]
    elif match_mode == "order" and plans:
        selected = plans[0]
    elif len(plans) == 1:
        selected = plans[0]

    keys = _classification_keys(query_norm, plans, selected)
    materials = []
    polimery = []
    lub_for_materials = (selected or {}).get("lub_number") or keys.get("lub_number")
    if lub_for_materials:
        materials = get_lub_farby(cur, lub_for_materials)
        polimery = get_lub_polimery(cur, lub_for_materials)

    related = {"by_lub": [], "by_artwork": []}
    if selected:
        related = _related_plans(cur, selected, exclude_ids={int(selected["id"])})

    # Widok klasyfikacji (LUB/artwork): zbieramy dane ze wszystkich pasujących zleceń.
    # Widok zlecenia: fokus na wybranym planie, ale historię powiązań zostawiamy w related.
    if selected and (match_mode == "order" or plan_id or len(plans) == 1):
        dossier_ids = [int(selected["id"])]
    else:
        dossier_ids = [int(p["id"]) for p in plans]

    dossier = _fetch_for_plan_ids(cur, dossier_ids)
    plans_view = _enrich_plan_summaries(cur, plans)

    selected_view = None
    selected_dossier = {
        "reports": [],
        "print_reports": [],
        "winding_reports": [],
        "events": [],
        "production_log": [],
        "issues": [],
        "roll_totals": {
            "roll_count": 0,
            "meters": 0.0,
            "pieces": 0,
            "ok_pieces": 0,
            "nok_pieces": 0,
        },
        "planned": {"meters": 0, "pieces": 0},
    }
    if selected:
        selected_view = next((p for p in plans_view if int(p["id"]) == int(selected["id"])), None)
        if selected_view is None:
            selected_view = _enrich_plan_summaries(cur, [selected])[0]
        selected_dossier = _fetch_for_plan_ids(cur, [int(selected["id"])])
        selected_dossier["roll_totals"] = sum_print_control_rolls(cur, int(selected["id"]))
        selected_dossier["planned"] = plan_production_targets(selected)

    return {
        "query": query_norm,
        "plan_id": int(selected["id"]) if selected else None,
        "match_mode": match_mode,
        "keys": keys,
        "plans": plans_view,
        "plans_count": len(plans_view),
        "plan": selected_view,
        "related_by_lub": related["by_lub"],
        "related_by_artwork": related["by_artwork"],
        "materials": materials,
        "polimery": polimery,
        "reports": selected_dossier["reports"] if selected else dossier["reports"],
        "print_reports": selected_dossier["print_reports"] if selected else dossier["print_reports"],
        "winding_reports": selected_dossier["winding_reports"] if selected else dossier["winding_reports"],
        "events": selected_dossier["events"] if selected else dossier["events"],
        "production_log": selected_dossier["production_log"] if selected else dossier["production_log"],
        "issues": selected_dossier["issues"] if selected else dossier["issues"],
        "roll_totals": selected_dossier.get("roll_totals") if selected else None,
        "planned": selected_dossier.get("planned") if selected else None,
        "aggregate": {
            "reports": dossier["reports"],
            "print_reports": dossier["print_reports"],
            "winding_reports": dossier["winding_reports"],
            "events": dossier["events"],
            "production_log": dossier["production_log"],
            "issues": dossier["issues"],
        },
    }


@router.get("/traceability")
def traceability(
    request: Request,
    query: str = Query(""),
    plan_id: int | None = Query(None),
    user=Depends(require_auth),
    conn=Depends(get_db),
):
    if not _can_view_traceability(user):
        return RedirectResponse("/dashboard", status_code=303)

    cur = conn.cursor()
    context = _build_traceability_context(cur, query, plan_id=plan_id)

    return render_template("traceability.html", {
        "user": {"username": user["username"], "role": user["role"]},
        "context": context,
    })
