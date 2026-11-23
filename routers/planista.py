"""
Router: panel planisty — katalog PDF/kolorów, color matching (informacyjny), drafty D6/D8/D10.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

from dependencies import get_db, require_planista_or_above
from helpers import PRODUCTION_MACHINES, render_template
from services.planner.color_match import match_selected_against_queues
from services.planner.fs_browse import browse_directory, normalize_folder_path
from services.planner.ingest import scan_pdf_folder
from services.planner.publish import publish_draft
from services.planner.queue_order import (
    insert_job_into_live_queue,
    reorder_draft_queue,
    reorder_live_queue,
)
from services.planner.settings import get_all_planner_settings, set_planner_setting
from services.runtime_env import planner_upload_dir

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
    from services.planner.color_match import load_machine_queues_ordered

    return load_machine_queues_ordered(cur)


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


def _draft_assigned_job_ids(cur, draft_id: int) -> set[int]:
    cur.execute(
        "SELECT color_job_id FROM plan_draft_items WHERE draft_id=? AND color_job_id IS NOT NULL",
        (draft_id,),
    )
    out: set[int] = set()
    for row in cur.fetchall():
        jid = _row_get(row, "color_job_id", 0)
        if jid is not None:
            out.add(int(jid))
    return out


def _live_assigned_job_ids(cur) -> set[int]:
    """color_jobs powiązane z aktywnymi planami maszyn (planned / in_progress)."""

    out: set[int] = set()
    cur.execute(
        """
        SELECT color_job_id, order_number FROM production_plans
        WHERE status IN ('planned', 'in_progress')
        """
    )
    order_numbers: list[str] = []
    for row in cur.fetchall():
        cj = _row_get(row, "color_job_id", 0)
        if cj is not None:
            out.add(int(cj))
            continue
        onum = (_row_get(row, "order_number", 1) or "").strip()
        if onum:
            order_numbers.append(onum)
    for onum in order_numbers:
        cur.execute(
            "SELECT id FROM color_jobs WHERE order_number=? ORDER BY id DESC LIMIT 1",
            (onum,),
        )
        jrow = cur.fetchone()
        if jrow:
            out.add(int(_row_get(jrow, "id", 0)))
    return out


def _pool_excluded_job_ids(cur, draft_id: int) -> set[int]:
    """Pula: ukryj prace już w draftcie lub na maszynie."""

    return _draft_assigned_job_ids(cur, draft_id) | _live_assigned_job_ids(cur)


def _verify_user_password(cur, username: str, password: str) -> bool:
    if not password:
        return False
    cur.execute("SELECT password FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    if not row:
        return False
    stored = _row_get(row, "password", 0) or ""
    return str(stored) == str(password)


def _remove_planned_for_job(cur, *, job_id: int | None, order_number: str | None) -> list[str]:
    """Usuwa tylko status=planned (nie in_progress). Zwraca etykiety usuniętych."""

    removed: list[str] = []
    onum = (order_number or "").strip()
    if job_id:
        cur.execute(
            """
            SELECT id, machine, order_number FROM production_plans
            WHERE status='planned' AND color_job_id=?
            """,
            (int(job_id),),
        )
        for row in cur.fetchall():
            pid = int(_row_get(row, "id", 0))
            machine = (_row_get(row, "machine", 1) or "").upper()
            label = _row_get(row, "order_number", 2) or str(pid)
            cur.execute("DELETE FROM production_plans WHERE id=? AND status='planned'", (pid,))
            removed.append(f"{label}@{machine}")
            if not onum:
                onum = (label or "").strip()
    if onum:
        cur.execute(
            """
            SELECT id, machine, order_number FROM production_plans
            WHERE status='planned' AND order_number=?
            """,
            (onum,),
        )
        for row in cur.fetchall():
            pid = int(_row_get(row, "id", 0))
            machine = (_row_get(row, "machine", 1) or "").upper()
            label = _row_get(row, "order_number", 2) or str(pid)
            key = f"{label}@{machine}"
            if key in removed:
                continue
            cur.execute("DELETE FROM production_plans WHERE id=? AND status='planned'", (pid,))
            removed.append(key)
    return removed


def _remove_draft_items_for_job(cur, *, job_id: int | None, order_number: str | None) -> list[int]:
    removed_ids: list[int] = []
    if job_id:
        cur.execute(
            "SELECT id FROM plan_draft_items WHERE color_job_id=?",
            (int(job_id),),
        )
        for row in cur.fetchall():
            iid = int(_row_get(row, "id", 0))
            cur.execute("DELETE FROM plan_draft_items WHERE id=?", (iid,))
            removed_ids.append(iid)
    onum = (order_number or "").strip()
    if onum:
        cur.execute(
            "SELECT id FROM plan_draft_items WHERE order_number=?",
            (onum,),
        )
        for row in cur.fetchall():
            iid = int(_row_get(row, "id", 0))
            if iid in removed_ids:
                continue
            cur.execute("DELETE FROM plan_draft_items WHERE id=?", (iid,))
            removed_ids.append(iid)
    return removed_ids


def _serialize_job(job, colors: list[str] | None = None) -> dict:
    jid = int(_row_get(job, "id", 0))
    return {
        "id": jid,
        "order_number": _row_get(job, "order_number") or "",
        "order_name": _row_get(job, "order_name") or "",
        "lub_number": _row_get(job, "lub_number") or "",
        "artwork_number": _row_get(job, "artwork_number") or "",
        "color_count": int(_row_get(job, "color_count") or 0),
        "extraction_status": _row_get(job, "extraction_status") or "",
        "thumbnail_path": _row_get(job, "thumbnail_path") or "",
        "colors": list(colors or []),
    }


def _serialize_draft_item(item) -> dict:
    return {
        "id": int(_row_get(item, "id", 0)),
        "machine": (_row_get(item, "machine") or "").upper(),
        "color_job_id": _row_get(item, "color_job_id"),
        "order_number": _row_get(item, "order_number") or "",
        "order_name": _row_get(item, "order_name") or "",
        "lub_number": _row_get(item, "lub_number") or "",
        "artwork_number": _row_get(item, "artwork_number") or "",
    }


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

    excluded_ids = _pool_excluded_job_ids(cur, draft_id)
    jobs = [
        j
        for j in _load_jobs(cur, q=q)
        if int(_row_get(j, "id", 0)) not in excluded_ids
    ]
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

    selected_job = None
    if selected:
        cur.execute("SELECT * FROM color_jobs WHERE id=?", (selected,))
        selected_job = cur.fetchone()

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
    try:
        if folder:
            set_planner_setting(cur, "planner_pdf_root", folder)
            conn.commit()
        summary = scan_pdf_folder(cur, folder or None, process=True, limit=120)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(f"/planista?error={_q('Skan nieudany: ' + str(exc)[:200])}", status_code=303)
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

    staging = planner_upload_dir()
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


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _add_job_to_draft(
    cur,
    user: dict,
    job_id: int,
    machine: str,
    *,
    before_item_id: int | None = None,
) -> tuple[dict | None, str | None]:
    machine = machine.upper().strip()
    if machine not in PRODUCTION_MACHINES:
        return None, "Nieprawidłowa maszyna"
    cur.execute("SELECT * FROM color_jobs WHERE id=?", (job_id,))
    job = cur.fetchone()
    if not job:
        return None, "Brak joba"
    draft_id = _ensure_open_draft(cur, user["username"])
    cur.execute(
        "SELECT id FROM plan_draft_items WHERE draft_id=? AND color_job_id=?",
        (draft_id, job_id),
    )
    if cur.fetchone():
        return None, "Job jest już w draftcie"

    cur.execute(
        """
        SELECT id FROM plan_draft_items
        WHERE draft_id=? AND UPPER(machine)=?
        ORDER BY sort_order, id
        """,
        (draft_id, machine),
    )
    existing_ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]

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
            0,
        ),
    )
    item_id = int(cur.lastrowid)
    if before_item_id and int(before_item_id) in existing_ids:
        idx = existing_ids.index(int(before_item_id))
        existing_ids.insert(idx, item_id)
    else:
        existing_ids.append(item_id)
    ok, err = reorder_draft_queue(
        cur, draft_id=draft_id, machine=machine, ordered_ids=existing_ids
    )
    if not ok:
        return None, err or "Nie udało się ustawić pozycji"

    return {
        "ok": True,
        "draft_id": draft_id,
        "item": {
            "id": item_id,
            "machine": machine,
            "color_job_id": job_id,
            "order_number": _row_get(job, "order_number") or "",
            "order_name": _row_get(job, "order_name") or "",
            "lub_number": _row_get(job, "lub_number") or "",
            "artwork_number": _row_get(job, "artwork_number") or "",
        },
        "job": _serialize_job(job),
    }, None


@router.post("/draft/add")
async def planista_draft_add(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    content_type = (request.headers.get("content-type") or "").lower()
    before_item_id = None
    if "application/json" in content_type:
        payload = await request.json()
        job_id = int(payload.get("job_id"))
        machine = str(payload.get("machine") or "")
        if payload.get("before_item_id") not in (None, ""):
            before_item_id = int(payload.get("before_item_id"))
    else:
        form = await request.form()
        job_id = int(form.get("job_id"))
        machine = str(form.get("machine") or "")
        raw_before = form.get("before_item_id")
        if raw_before not in (None, ""):
            before_item_id = int(raw_before)

    cur = conn.cursor()
    result, err = _add_job_to_draft(
        cur, user, job_id, machine, before_item_id=before_item_id
    )
    if err:
        conn.rollback()
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        return RedirectResponse(f"/planista?error={_q(err)}", status_code=303)
    conn.commit()
    if _wants_json(request):
        return JSONResponse(result)
    return RedirectResponse(
        f"/planista?success=Dodano+do+{machine.upper()}",
        status_code=303,
    )


@router.post("/live/insert")
async def planista_live_insert(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Wstawia pracę z puli bezpośrednio do kolejki live we wskazanej pozycji."""

    payload = await request.json()
    job_id = int(payload.get("job_id"))
    machine = str(payload.get("machine") or "")
    before_plan_id = payload.get("before_plan_id")
    before_plan_id = int(before_plan_id) if before_plan_id not in (None, "") else None
    also_draft = bool(payload.get("also_draft", True))

    cur = conn.cursor()
    cur.execute("SELECT * FROM color_jobs WHERE id=?", (job_id,))
    job = cur.fetchone()
    if not job:
        return JSONResponse({"ok": False, "error": "Brak joba"}, status_code=404)

    live, err = insert_job_into_live_queue(
        cur, job_row=job, machine=machine, before_plan_id=before_plan_id
    )
    if err:
        conn.rollback()
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    draft_item = None
    if also_draft:
        # Zsynchronizuj draft: dodaj jeśli brak, wstaw przed odpowiadającym itemem
        before_item_id = None
        if before_plan_id:
            cur.execute(
                "SELECT order_number FROM production_plans WHERE id=?",
                (before_plan_id,),
            )
            prow = cur.fetchone()
            onum = (_row_get(prow, "order_number", 0) or "").strip() if prow else ""
            if onum:
                draft_id = _ensure_open_draft(cur, user["username"])
                cur.execute(
                    """
                    SELECT id FROM plan_draft_items
                    WHERE draft_id=? AND UPPER(machine)=? AND order_number=?
                    ORDER BY sort_order, id LIMIT 1
                    """,
                    (draft_id, machine.upper(), onum),
                )
                drow = cur.fetchone()
                if drow:
                    before_item_id = int(_row_get(drow, "id", 0))
        draft_result, draft_err = _add_job_to_draft(
            cur, user, job_id, machine, before_item_id=before_item_id
        )
        if draft_err and "już w draftcie" not in (draft_err or ""):
            # Live już wstawione — draft opcjonalny; nie wycofuj live przy „już w draftcie”
            pass
        elif draft_result:
            draft_item = draft_result.get("item")

    conn.commit()
    colors = _job_colors_map(cur, [job_id]).get(job_id, [])
    return JSONResponse(
        {
            "ok": True,
            "live": live,
            "item": draft_item,
            "job": _serialize_job(job, colors),
        }
    )


@router.post("/draft/remove")
async def planista_draft_remove(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Usunięcie z draftu BEZ przywracania do puli (praca nadal ukryta jeśli jest na maszynie)."""

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        item_id = int(payload.get("item_id"))
    else:
        form = await request.form()
        item_id = int(form.get("item_id"))

    cur = conn.cursor()
    cur.execute("SELECT * FROM plan_draft_items WHERE id=?", (item_id,))
    item = cur.fetchone()
    if not item:
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "Brak pozycji"}, status_code=404)
        return RedirectResponse("/planista?error=Brak+pozycji", status_code=303)

    cur.execute("DELETE FROM plan_draft_items WHERE id=?", (item_id,))
    conn.commit()
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "item_id": item_id,
                "restored_to_pool": False,
                "machine": (_row_get(item, "machine") or "").upper(),
            }
        )
    return RedirectResponse("/planista?success=Usuni%C4%99to+z+draftu", status_code=303)


@router.post("/pool/restore")
async def planista_pool_restore(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Przywraca pracę do puli po podaniu hasła — zdejmuje z draftu i planned na maszynie."""

    payload = await request.json()
    password = str(payload.get("password") or "")
    item_id = payload.get("item_id")
    plan_id = payload.get("plan_id")
    job_id = payload.get("job_id")

    cur = conn.cursor()
    if not _verify_user_password(cur, user["username"], password):
        return JSONResponse({"ok": False, "error": "Nieprawidłowe hasło"}, status_code=403)

    order_number = None
    resolved_job_id = int(job_id) if job_id not in (None, "") else None
    machine = None

    if item_id not in (None, ""):
        cur.execute("SELECT * FROM plan_draft_items WHERE id=?", (int(item_id),))
        item = cur.fetchone()
        if not item:
            return JSONResponse({"ok": False, "error": "Brak pozycji draftu"}, status_code=404)
        if resolved_job_id is None and _row_get(item, "color_job_id") is not None:
            resolved_job_id = int(_row_get(item, "color_job_id"))
        order_number = _row_get(item, "order_number") or ""
        machine = (_row_get(item, "machine") or "").upper()

    if plan_id not in (None, ""):
        cur.execute("SELECT * FROM production_plans WHERE id=?", (int(plan_id),))
        plan = cur.fetchone()
        if not plan:
            return JSONResponse({"ok": False, "error": "Brak pozycji live"}, status_code=404)
        status = _row_get(plan, "status") or ""
        if status == "in_progress":
            return JSONResponse(
                {"ok": False, "error": "Nie można przywrócić pracy w toku"},
                status_code=400,
            )
        if resolved_job_id is None and _row_get(plan, "color_job_id") is not None:
            resolved_job_id = int(_row_get(plan, "color_job_id"))
        order_number = order_number or (_row_get(plan, "order_number") or "")
        machine = machine or (_row_get(plan, "machine") or "").upper()

    if resolved_job_id is None and order_number:
        cur.execute(
            "SELECT id FROM color_jobs WHERE order_number=? ORDER BY id DESC LIMIT 1",
            (order_number.strip(),),
        )
        jrow = cur.fetchone()
        if jrow:
            resolved_job_id = int(_row_get(jrow, "id", 0))

    if resolved_job_id is None and not order_number:
        return JSONResponse({"ok": False, "error": "Brak pracy do przywrócenia"}, status_code=400)

    # Blokada: in_progress
    blocked = False
    if resolved_job_id:
        cur.execute(
            "SELECT id FROM production_plans WHERE status='in_progress' AND color_job_id=? LIMIT 1",
            (resolved_job_id,),
        )
        blocked = cur.fetchone() is not None
    if not blocked and (order_number or "").strip():
        cur.execute(
            "SELECT id FROM production_plans WHERE status='in_progress' AND order_number=? LIMIT 1",
            ((order_number or "").strip(),),
        )
        blocked = cur.fetchone() is not None
    if blocked:
        return JSONResponse(
            {"ok": False, "error": "Praca jest w toku na maszynie — najpierw ją zakończ"},
            status_code=400,
        )

    draft_removed = _remove_draft_items_for_job(
        cur, job_id=resolved_job_id, order_number=order_number
    )
    live_removed = _remove_planned_for_job(
        cur, job_id=resolved_job_id, order_number=order_number
    )

    job_payload = None
    if resolved_job_id:
        cur.execute("SELECT * FROM color_jobs WHERE id=?", (resolved_job_id,))
        job = cur.fetchone()
        if job:
            colors = _job_colors_map(cur, [resolved_job_id]).get(resolved_job_id, [])
            job_payload = _serialize_job(job, colors)

    conn.commit()
    return JSONResponse(
        {
            "ok": True,
            "restored_to_pool": True,
            "job": job_payload,
            "draft_removed_ids": draft_removed,
            "live_removed": live_removed,
            "machine": machine,
        }
    )


def _serialize_live_plan(row) -> dict:
    return {
        "id": int(_row_get(row, "id", 0)),
        "machine": (_row_get(row, "machine") or "").upper(),
        "order_number": _row_get(row, "order_number") or "",
        "order_name": _row_get(row, "order_name") or "",
        "lub_number": _row_get(row, "lub_number") or "",
        "artwork_number": _row_get(row, "artwork_number") or "",
        "status": _row_get(row, "status") or "",
        "sort_order": int(_row_get(row, "sort_order") or 0),
        "color_job_id": _row_get(row, "color_job_id"),
        "farby_prep_status": _row_get(row, "farby_prep_status") or "pending",
        "polimery_prep_status": _row_get(row, "polimery_prep_status") or "pending",
        "assortment_prep_status": _row_get(row, "assortment_prep_status") or "pending",
    }


@router.get("/api/live-queues")
def planista_api_live_queues(
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Aktualny stan kolejek D6/D8/D10 — ten sam źródłowy co widok maszyny."""

    cur = conn.cursor()
    queues = _load_machine_queues(cur)
    payload = {
        "ok": True,
        "machines": {
            m: [_serialize_live_plan(row) for row in (queues.get(m) or [])]
            for m in PRODUCTION_MACHINES
        },
    }
    return JSONResponse(payload)


def _resolve_color_job_id_for_match(cur, *, job_id=None, plan_id=None, draft_item_id=None):
    if job_id:
        return int(job_id), None
    if plan_id:
        cur.execute("SELECT color_job_id, order_number FROM production_plans WHERE id=?", (int(plan_id),))
        row = cur.fetchone()
        if not row:
            return None, "Brak pozycji w kolejce live"
        cj = _row_get(row, "color_job_id", 0)
        if cj is not None:
            return int(cj), None
        onum = (_row_get(row, "order_number", 1) or "").strip()
        if not onum:
            return None, "Brak powiązania z katalogiem kolorów"
        cur.execute(
            "SELECT id FROM color_jobs WHERE order_number=? ORDER BY id DESC LIMIT 1",
            (onum,),
        )
        jrow = cur.fetchone()
        if not jrow:
            return None, "Brak PDF/katalogu kolorów dla tej pracy"
        return int(_row_get(jrow, "id", 0)), None
    if draft_item_id:
        cur.execute(
            "SELECT color_job_id, order_number FROM plan_draft_items WHERE id=?",
            (int(draft_item_id),),
        )
        row = cur.fetchone()
        if not row:
            return None, "Brak pozycji w draftcie"
        cj = _row_get(row, "color_job_id", 0)
        if cj is not None:
            return int(cj), None
        onum = (_row_get(row, "order_number", 1) or "").strip()
        if not onum:
            return None, "Brak powiązania z katalogiem kolorów"
        cur.execute(
            "SELECT id FROM color_jobs WHERE order_number=? ORDER BY id DESC LIMIT 1",
            (onum,),
        )
        jrow = cur.fetchone()
        if not jrow:
            return None, "Brak PDF/katalogu kolorów dla tej pracy"
        return int(_row_get(jrow, "id", 0)), None
    return None, "Podaj job_id, plan_id lub draft_item_id"


@router.get("/api/color-match")
def planista_api_color_match(
    job_id: int | None = Query(None),
    plan_id: int | None = Query(None),
    draft_item_id: int | None = Query(None),
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Porównanie wybranej pracy (pula / live / draft) z kolejkami — bez sortowania."""

    cur = conn.cursor()
    resolved_id, err = _resolve_color_job_id_for_match(
        cur, job_id=job_id, plan_id=plan_id, draft_item_id=draft_item_id
    )
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=404)
    draft_id = _ensure_open_draft(cur, user["username"])
    conn.commit()
    payload = match_selected_against_queues(cur, int(resolved_id), draft_id=draft_id)
    if not payload.get("ok"):
        return JSONResponse(payload, status_code=404)
    payload["source"] = {
        "job_id": resolved_id,
        "plan_id": plan_id,
        "draft_item_id": draft_item_id,
    }
    return JSONResponse(payload)


@router.get("/api/similar")
def planista_api_similar(
    job_id: int | None = Query(None),
    plan_id: int | None = Query(None),
    draft_item_id: int | None = Query(None),
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Alias kompatybilności → color-match (bez rankingu / Jaccarda)."""

    return planista_api_color_match(
        job_id=job_id,
        plan_id=plan_id,
        draft_item_id=draft_item_id,
        user=user,
        conn=conn,
    )


@router.post("/queue/reorder")
async def planista_queue_reorder(
    request: Request,
    user=Depends(require_planista_or_above),
    conn=Depends(get_db),
):
    """Zmiana kolejności w kolejce live lub draft (bez auto-rekomendacji)."""

    payload = await request.json()
    machine = str(payload.get("machine") or "")
    scope = str(payload.get("scope") or "").lower().strip()
    ordered_ids = payload.get("ordered_ids") or []
    try:
        ordered_ids = [int(x) for x in ordered_ids]
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Nieprawidłowe ID"}, status_code=400)

    cur = conn.cursor()
    if scope == "live":
        ok, err = reorder_live_queue(cur, machine, ordered_ids)
    elif scope == "draft":
        draft_id = _ensure_open_draft(cur, user["username"])
        ok, err = reorder_draft_queue(
            cur, draft_id=draft_id, machine=machine, ordered_ids=ordered_ids
        )
    else:
        return JSONResponse({"ok": False, "error": "scope=live|draft"}, status_code=400)

    if not ok:
        conn.rollback()
        return JSONResponse({"ok": False, "error": err or "Błąd"}, status_code=400)
    conn.commit()
    return JSONResponse({"ok": True, "machine": machine.upper(), "scope": scope, "ordered_ids": ordered_ids})


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
