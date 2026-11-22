"""
Router: panel planisty — katalog PDF/kolorów, similarity, drafty D6/D8/D10, publish merge.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

from dependencies import get_db, require_planista_or_above
from helpers import PRODUCTION_MACHINES, get_base_path, render_template
from services.planner.fs_browse import browse_directory, normalize_folder_path
from services.planner.ingest import scan_pdf_folder
from services.planner.publish import publish_draft
from services.planner.settings import get_all_planner_settings, set_planner_setting
from services.planner.similarity_service import suggest_similar

router = APIRouter(prefix="/planista")


def _row_get(row, key, index=None, default=None):
    if row is None:
        return default
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return default
    if index is not None:
        try:
            return row[index]
        except (IndexError, TypeError):
            return default
    return default


def _ensure_open_draft(cur, username: str) -> int:
    cur.execute(
        "SELECT id FROM plan_drafts WHERE status='open' ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row:
        return int(_row_get(row, "id", 0))
    cur.execute(
        "INSERT INTO plan_drafts (name, status, created_by) VALUES (?, 'open', ?)",
        ("Roboczy", username),
    )
    return int(cur.lastrowid)


def _load_machine_queues(cur) -> dict[str, list]:
    queues: dict[str, list] = {m: [] for m in PRODUCTION_MACHINES}
    cur.execute(
        """
        SELECT id, machine, order_number, artwork_number, lub_number, order_name, status
        FROM production_plans
        WHERE status IN ('planned', 'in_progress')
        ORDER BY CASE WHEN status='in_progress' THEN 0 ELSE 1 END, id
        """
    )
    for row in cur.fetchall():
        machine = _row_get(row, "machine", 1)
        if machine in queues:
            queues[machine].append(row)
    return queues


def _load_draft_items(cur, draft_id: int) -> dict[str, list]:
    items: dict[str, list] = {m: [] for m in PRODUCTION_MACHINES}
    cur.execute(
        """
        SELECT * FROM plan_draft_items
        WHERE draft_id=?
        ORDER BY sort_order, id
        """,
        (draft_id,),
    )
    for row in cur.fetchall():
        machine = (_row_get(row, "machine") or "").upper()
        if machine in items:
            items[machine].append(row)
    return items


def _load_jobs(cur, *, q: str | None = None, limit: int = 120) -> list:
    if q:
        like = f"%{q.strip()}%"
        cur.execute(
            """
            SELECT * FROM color_jobs
            WHERE order_number LIKE ? OR order_name LIKE ? OR lub_number LIKE ?
               OR artwork_number LIKE ? OR source_path LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (like, like, like, like, like, limit),
        )
    else:
        cur.execute("SELECT * FROM color_jobs ORDER BY id DESC LIMIT ?", (limit,))
    return list(cur.fetchall())


def _job_colors_map(cur, job_ids: list[int]) -> dict[int, list[str]]:
    if not job_ids:
        return {}
    out: dict[int, list[str]] = {jid: [] for jid in job_ids}
    placeholders = ",".join("?" for _ in job_ids)
    cur.execute(
        f"""
        SELECT color_job_id, color_name FROM color_separations
        WHERE color_job_id IN ({placeholders})
        ORDER BY sort_order, id
        """,
        tuple(job_ids),
    )
    for row in cur.fetchall():
        jid = int(_row_get(row, "color_job_id", 0))
        name = _row_get(row, "color_name", 1)
        if jid in out and name:
            out[jid].append(name)
    return out


@router.get("")
def planista_panel(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    q: str | None = Query(None),
    selected: int | None = Query(None),
    success: str | None = Query(None),
    error: str | None = Query(None),
):
    cur = conn.cursor()
    draft_id = _ensure_open_draft(cur, user["username"])
    conn.commit()

    jobs = _load_jobs(cur, q=q)
    job_ids = [int(_row_get(j, "id", 0)) for j in jobs]
    colors = _job_colors_map(cur, job_ids)
    queues = _load_machine_queues(cur)
    draft_items = _load_draft_items(cur, draft_id)
    settings = get_all_planner_settings(cur)

    cur.execute(
        """
        SELECT status, COUNT(*) AS cnt FROM pdf_ingest_files
        GROUP BY status
        """
    )
    ingest_counts = {
        (_row_get(r, "status", 0) or ""): int(_row_get(r, "cnt", 1) or 0)
        for r in cur.fetchall()
    }

    similar = []
    selected_job = None
    if selected:
        cur.execute("SELECT * FROM color_jobs WHERE id=?", (selected,))
        selected_job = cur.fetchone()
        if selected_job:
            similar = suggest_similar(cur, int(selected), limit=10)

    return render_template(
        "planista.html",
        {
            "user": user,
            "jobs": jobs,
            "colors": colors,
            "queues": queues,
            "draft_id": draft_id,
            "draft_items": draft_items,
            "machines": list(PRODUCTION_MACHINES),
            "settings": settings,
            "ingest_counts": ingest_counts,
            "q": q or "",
            "selected": selected,
            "selected_job": selected_job,
            "similar": similar,
            "success": success,
            "error": error,
        },
    )


@router.get("/api/browse")
def planista_browse(
    path: str = Query(""),
    user=Depends(require_planista_or_above),
):
    return JSONResponse(browse_directory(path or None))


@router.post("/scan")
def planista_scan(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    folder: str = Form(""),
):
    cur = conn.cursor()
    folder = normalize_folder_path(folder)
    if folder:
        set_planner_setting(cur, "planner_pdf_root", folder)
    summary = scan_pdf_folder(cur, folder or None, process=True, limit=40)
    conn.commit()
    msg = summary.as_text()
    if summary.missing_root:
        return RedirectResponse(f"/planista?error={_q(msg)}", status_code=303)
    return RedirectResponse(f"/planista?success={_q('Skan: ' + msg)}", status_code=303)


@router.post("/upload-pdfs")
async def planista_upload_pdfs(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    files: list[UploadFile] = File(...),
):
    """Import testowy: zapis PDF do lokalnego folderu staging i od razu skan."""

    staging = Path(get_base_path()) / "data" / "planner_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    saved = 0
    for upload in files:
        name = (upload.filename or "").strip()
        if not name.lower().endswith(".pdf"):
            continue
        safe = Path(name).name
        target = staging / safe
        content = await upload.read()
        if not content:
            continue
        target.write_bytes(content)
        saved += 1
    cur = conn.cursor()
    set_planner_setting(cur, "planner_pdf_root", str(staging))
    summary = scan_pdf_folder(cur, str(staging), process=True, limit=80)
    conn.commit()
    if saved == 0:
        return RedirectResponse("/planista?error=Nie+wgrano+plik%C3%B3w+PDF", status_code=303)
    return RedirectResponse(
        f"/planista?success={_q(f'Wgrano {saved} PDF. {summary.as_text()}')}",
        status_code=303,
    )


@router.post("/draft/add")
def planista_draft_add(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    job_id: int = Form(...),
    machine: str = Form(...),
):
    cur = conn.cursor()
    machine = machine.upper().strip()
    if machine not in PRODUCTION_MACHINES:
        return RedirectResponse("/planista?error=Nieprawid%C5%82owa+maszyna", status_code=303)
    cur.execute("SELECT * FROM color_jobs WHERE id=?", (job_id,))
    job = cur.fetchone()
    if not job:
        return RedirectResponse("/planista?error=Brak+joba", status_code=303)
    draft_id = _ensure_open_draft(cur, user["username"])
    cur.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM plan_draft_items WHERE draft_id=? AND machine=?",
        (draft_id, machine),
    )
    max_sort = cur.fetchone()
    next_sort = int(_row_get(max_sort, "m", 0) or -1) + 1
    cur.execute(
        """
        INSERT INTO plan_draft_items
            (draft_id, machine, color_job_id, order_number, artwork_number, lub_number, order_name, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            machine,
            job_id,
            _row_get(job, "order_number"),
            _row_get(job, "artwork_number"),
            _row_get(job, "lub_number"),
            _row_get(job, "order_name"),
            next_sort,
        ),
    )
    conn.commit()
    return RedirectResponse(f"/planista?selected={job_id}&success=Dodano+do+{machine}", status_code=303)


@router.post("/draft/remove")
def planista_draft_remove(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    item_id: int = Form(...),
):
    cur = conn.cursor()
    cur.execute("DELETE FROM plan_draft_items WHERE id=?", (item_id,))
    conn.commit()
    return RedirectResponse("/planista?success=Usuni%C4%99to+z+draftu", status_code=303)


@router.post("/publish")
def planista_publish(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    draft_id: int = Form(...),
):
    cur = conn.cursor()
    report = publish_draft(cur, draft_id, published_by=user["username"])
    # Po publish otwórz nowy draft
    cur.execute(
        "INSERT INTO plan_drafts (name, status, created_by) VALUES (?, 'open', ?)",
        ("Roboczy", user["username"]),
    )
    conn.commit()
    msg = f"Publish: {report.as_text()}"
    if report.conflicts:
        msg += " | Konflikty: " + "; ".join(report.conflicts[:5])
    return RedirectResponse(f"/planista?success={_q(msg)}", status_code=303)


@router.post("/settings")
def planista_settings(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
    pdf_root: str = Form(""),
    scan_interval_min: str = Form("5"),
    watcher_enabled: str = Form("0"),
):
    cur = conn.cursor()
    set_planner_setting(cur, "planner_pdf_root", normalize_folder_path(pdf_root))
    set_planner_setting(cur, "planner_scan_interval_min", str(scan_interval_min).strip() or "5")
    set_planner_setting(cur, "planner_watcher_enabled", "1" if watcher_enabled in ("1", "on", "true") else "0")
    conn.commit()
    return RedirectResponse("/planista?success=Zapisano+ustawienia", status_code=303)


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:300])
