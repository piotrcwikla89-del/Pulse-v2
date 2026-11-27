"""
Router: panel admina — zarządzanie użytkownikami, import danych, ustawienia powiadomień.
"""
import csv
import io

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from db_compat import INTEGRITY_ERRORS
from dependencies import get_db, require_admin
from helpers import (
    NOTIFICATION_EVENT_LABELS,
    PRODUCTION_MACHINES,
    dodaj_operacje,
    get_edit_password,
    render_template,
)
from services.planner.fs_browse import normalize_folder_path
from services.planner.ingest import (
    ingest_status_counts,
    pending_ingest_count,
    scan_pdf_folder,
)
from services.planner.lub_import import import_lub_wykaz_bytes, lub_import_status
from services.planner.settings import get_planner_setting, set_planner_setting

router = APIRouter(prefix="/admin")


@router.get("")
def admin_panel(
    request: Request,
    admin=Depends(require_admin),
    error: str = Query(None),
    success: str = Query(None),
    pdf_continue: str = Query(None),
    conn=Depends(get_db),
):
    cur = conn.cursor()
    cur.execute("SELECT id, username, role FROM users ORDER BY role DESC, username")
    users = cur.fetchall()
    cur.execute("SELECT name, start_time, end_time FROM shifts ORDER BY start_time")
    shifts = cur.fetchall()
    cur.execute("SELECT event_key, enabled FROM notification_settings")
    settings_map = {r["event_key"]: bool(r["enabled"]) for r in cur.fetchall()}
    notification_prefs = [
        {"key": k, "label": v, "enabled": settings_map.get(k, True)}
        for k, v in NOTIFICATION_EVENT_LABELS.items()
    ]
    pdf_errors = []
    try:
        cur.execute(
            """
            SELECT file_name, last_error, processed_at
            FROM pdf_ingest_files
            WHERE status='error' AND last_error IS NOT NULL
            ORDER BY id DESC
            LIMIT 8
            """
        )
        pdf_errors = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        pdf_errors = []
    context = {
        "users": users,
        "shifts": shifts,
        "notification_prefs": notification_prefs,
        "edit_password_current": get_edit_password(cur),
        "user": {"username": request.session.get("username"), "role": request.session.get("role")},
        "planner_pdf_root": get_planner_setting(cur, "planner_pdf_root"),
        "planner_last_scan": get_planner_setting(cur, "planner_last_scan_at"),
        "planner_last_summary": get_planner_setting(cur, "planner_last_scan_summary"),
        "lub_status": lub_import_status(cur),
        "pdf_ingest": ingest_status_counts(cur),
        "pdf_errors": pdf_errors,
        "pdf_continue": str(pdf_continue or "") in ("1", "true", "yes"),
    }
    if error:
        context["error"] = error
    if success:
        context["success"] = success
    return render_template("admin_panel.html", context)


@router.post("/add_operator")
def add_operator(request: Request, username: str = Form(...), admin=Depends(require_admin)):
    return RedirectResponse("/admin?error=U%C5%BCyj+formularza+dodawania+u%C5%BCytkownika", status_code=303)


@router.post("/add_user")
def add_user(
    request: Request,
    username: str = Form(...),
    role: str = Form(...),
    password: str = Form(""),
    confirm_password: str = Form(""),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    if role not in ["drukarz", "operator_przewijarki", "operator_mieszalni", "prepress", "manager", "admin", "planista"]:
        return RedirectResponse("/admin?error=Nieprawid%C5%82owa+rola", status_code=303)
    if not password or not confirm_password or password != confirm_password:
        return RedirectResponse("/admin?error=Has%C5%82a+niezgodne+lub+puste", status_code=303)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, role, password) VALUES (?, ?, ?)",
            (username, role, password),
        )
        conn.commit()
    except INTEGRITY_ERRORS:
        return RedirectResponse("/admin?error=U%C5%BCytkownik+ju%C5%BC+istnieje", status_code=303)
    return RedirectResponse("/admin?success=U%C5%BCytkownik+dodany", status_code=303)


@router.post("/notification-settings")
async def admin_notification_settings(
    request: Request,
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    form = await request.form()
    cur = conn.cursor()
    for key in NOTIFICATION_EVENT_LABELS:
        enabled = 1 if form.get(key) == "on" else 0
        cur.execute("UPDATE notification_settings SET enabled=? WHERE event_key=?", (enabled, key))
    conn.commit()
    return RedirectResponse("/admin?success=Zapisano+ustawienia+powiadomie%C5%84", status_code=303)


@router.post("/delete_user")
def delete_user(
    request: Request,
    user_id: int = Form(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    if user and user["role"] != "admin":
        cur.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/change_password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    cur = conn.cursor()
    cur.execute("SELECT password FROM users WHERE username=? AND role='admin'", (admin["username"],))
    db_user = cur.fetchone()
    if db_user and db_user["password"] == current_password:
        cur.execute(
            "UPDATE users SET password=? WHERE username=? AND role='admin'",
            (new_password, admin["username"]),
        )
        conn.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/set-edit-password")
def set_edit_password(
    request: Request,
    new_edit_password: str = Form(...),
    confirm_edit_password: str = Form(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    if new_edit_password != confirm_edit_password:
        return RedirectResponse("/admin?error=Has%C5%82a+niezgodne", status_code=303)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('edit_password', ?)",
        (new_edit_password,),
    )
    conn.commit()
    return RedirectResponse("/admin?success=Has%C5%82o+korekty+zmienione", status_code=303)


@router.post("/import_farby")
def import_farby(
    request: Request,
    plik: UploadFile = File(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    """CSV (stary) albo Excel Magazyn_farb_resztkowych (.xlsx/.xlsm) — replace żywego stanu."""

    from urllib.parse import quote

    name = (plik.filename or "").lower()
    cur = conn.cursor()
    try:
        raw = plik.file.read()
        if name.endswith(".xlsx") or name.endswith(".xlsm"):
            from services.magazyn_import import import_magazyn_excel

            report = import_magazyn_excel(
                cur,
                raw,
                imported_by=request.session.get("username") or "admin",
            )
            if report.errors:
                conn.rollback()
                return RedirectResponse(
                    f"/admin?error={quote(report.errors[0])}",
                    status_code=303,
                )
            conn.commit()
            return RedirectResponse(
                f"/admin?success={quote(report.as_text())}",
                status_code=303,
            )

        from services.magazyn_import import ensure_farby_artwork_column

        ensure_farby_artwork_column(cur)
        content = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        count = 0
        for row in reader:
            pantone = row.get("pantone") or row.get("pantone".upper()) or row.get("PANTONE")
            lub = row.get("lub", "")
            polka = row.get("polka", "")
            artwork = row.get("artwork", "") or row.get("Artwork", "")
            waga = row.get("waga")
            data_produkcji = row.get("data_produkcji") or row.get("data_produkcji".replace("_", ""))
            if not pantone or not waga or not data_produkcji:
                continue
            try:
                waga_val = float(waga)
            except ValueError:
                continue
            cur.execute(
                "INSERT INTO farby (pantone, lub, artwork, polka, waga, status, data_produkcji) VALUES (?, ?, ?, ?, ?, 'dostepna', ?)",
                (pantone, lub, artwork or None, polka, waga_val, data_produkcji),
            )
            dodaj_operacje(cur, "przyjęcie", pantone, str(waga_val), polka, "", cur.lastrowid)
            count += 1
        conn.commit()
        return RedirectResponse(f"/admin?success=Zaimportowano+{count}+farb", status_code=303)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(f"/admin?error={quote('Błąd importu farb: ' + str(e)[:200])}", status_code=303)


@router.post("/import_polimery")
def import_polimery(
    request: Request,
    plik: UploadFile = File(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    try:
        content = plik.file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        cur = conn.cursor()
        count = 0
        for row in reader:
            lub = row.get("lub") or row.get("LUB")
            kolor = row.get("kolor") or row.get("Kolor")
            lokalizacja = row.get("lokalizacja", "")
            data_waznosci = row.get("data_waznosci") or row.get("data_waznosci".replace("_", ""))
            uwagi = row.get("uwagi", "")
            if not lub or not kolor:
                continue
            cur.execute(
                "INSERT INTO polymers (lub, kolor, status, lokalizacja, data_waznosci, uwagi) VALUES (?, ?, 'dostepna', ?, ?, ?)",
                (lub, kolor, lokalizacja, data_waznosci, uwagi),
            )
            new_id = cur.lastrowid
            cur.execute(
                "INSERT INTO polymer_operations (typ, polymer_id, lokalizacja, uwagi) VALUES ('przyjęcie', ?, ?, ?)",
                (new_id, lokalizacja, f"Import CSV: LUB={lub}, kolor={kolor}"),
            )
            count += 1
        conn.commit()
        return RedirectResponse(f"/admin?success=Zaimportowano+{count}+polimerów", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin?error=Błąd+importu+polimerów:+{str(e)}", status_code=303)


@router.post("/planner-scan")
def admin_planner_scan(
    request: Request,
    folder: str = Form(...),
    save_as_root: str = Form(""),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    from urllib.parse import quote

    cur = conn.cursor()
    folder = normalize_folder_path(folder)
    if not folder:
        return RedirectResponse("/admin?error=Podaj+folder+PDF", status_code=303)
    try:
        if save_as_root in ("1", "on", "true"):
            set_planner_setting(cur, "planner_pdf_root", folder)
            conn.commit()
        # Tylko rejestracja — OCR idzie partiami z /admin/planner-process-batch (bez 502).
        summary = scan_pdf_folder(cur, folder, process=False, limit=0, requeue_weak=True)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(
            f"/admin?error={quote('Skan PDF nieudany: ' + str(exc)[:200])}",
            status_code=303,
        )

    if summary.missing_root:
        return RedirectResponse(f"/admin?error={quote(summary.as_text())}", status_code=303)
    pending = pending_ingest_count(cur)
    msg = summary.as_text()
    if pending:
        msg += f" · OCR w kolejce workera ({pending})"
    return RedirectResponse(f"/admin?success={quote('Skan PDF: ' + msg)}", status_code=303)


@router.post("/planner-process-batch")
def admin_planner_process_batch(
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    """Status kolejki PDF — OCR nie działa w HTTP (tylko background worker)."""

    cur = conn.cursor()
    counts = ingest_status_counts(cur)
    return {
        "ok": True,
        "busy": False,
        "processed": 0,
        "failed": 0,
        "pending": counts.get("pending", 0),
        "done": counts.get("done", 0),
        "error": counts.get("error", 0),
        "worker": True,
        "message": "OCR i miniatury robi osobny worker — web tylko kolejkę.",
    }


@router.post("/upload-pdfs")
async def admin_upload_pdfs(
    request: Request,
    admin=Depends(require_admin),
    conn=Depends(get_db),
    files: list[UploadFile] = File(...),
):
    """Wgrywa PDF do stagingu, rejestruje je i odpala pierwszą partię OCR."""

    from pathlib import Path
    from urllib.parse import quote

    from services.runtime_env import planner_upload_dir

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
    if saved == 0:
        return RedirectResponse("/admin?error=Nie+wgrano+plik%C3%B3w+PDF", status_code=303)

    cur = conn.cursor()
    from services.planner.pdf_blobs import ensure_blob_columns

    ensure_blob_columns(cur)
    set_planner_setting(cur, "planner_pdf_root", str(staging))
    # Szybka rejestracja + blob w DB (worker OCR na Render nie widzi dysku web).
    summary = scan_pdf_folder(cur, str(staging), process=False, limit=0, requeue_weak=False)
    conn.commit()
    pending = pending_ingest_count(cur)
    msg = f"Wgrano {saved} PDF. {summary.as_text()}"
    if pending:
        msg += f" · OCR w kolejce workera ({pending}; PDF w bazie)"
    return RedirectResponse(f"/admin?success={quote(msg)}", status_code=303)


@router.post("/import_plany")
def import_plany(
    request: Request,
    plik: UploadFile = File(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    try:
        content = plik.file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        cur = conn.cursor()
        count = 0
        for row in reader:
            machine = (row.get("machine") or row.get("maszyna") or "").upper()
            order_number = row.get("order_number") or row.get("numer_zlecenia")
            artwork_number = row.get("artwork_number") or row.get("numer_artwork")
            lub_number = row.get("lub_number") or row.get("numer_lub")
            order_name = row.get("order_name") or row.get("nazwa_zlecenia")
            laminate = row.get("laminate") or row.get("laminat")
            meters = row.get("meters") or row.get("ilosc_metrow") or 0
            pieces = row.get("pieces") or row.get("ilosc_sztuk") or 0
            planned_date = row.get("planned_date") or row.get("data_planowana")
            if machine not in PRODUCTION_MACHINES or not order_number:
                continue
            try:
                meters_val = int(meters)
                pieces_val = int(pieces)
            except ValueError:
                continue
            cur.execute(
                """INSERT INTO production_plans (machine, order_number, artwork_number, lub_number, order_name, laminate, meters, pieces, planned_date, status, assortment_prep_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', 'pending')""",
                (machine, order_number, artwork_number, lub_number, order_name, laminate, meters_val, pieces_val, planned_date),
            )
            count += 1
        conn.commit()
        return RedirectResponse(f"/admin?success=Zaimportowano+{count}+planów", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin?error=Błąd+importu+planów:+{str(e)}", status_code=303)


@router.post("/import_lub_wykaz")
def import_lub_wykaz(
    request: Request,
    plik: UploadFile = File(...),
    admin=Depends(require_admin),
    conn=Depends(get_db),
):
    """Import arkusza WYKAZ (LUB ↔ ARTWORK) + wiązanie z planami / PDF / draftami."""

    from urllib.parse import quote

    cur = conn.cursor()
    try:
        content = plik.file.read()
        if not content:
            return RedirectResponse("/admin?error=Pusty+plik", status_code=303)
        report = import_lub_wykaz_bytes(
            cur,
            content,
            filename=plik.filename or "Wykaz_LUB.xlsx",
            imported_by=admin.get("username"),
            link=True,
        )
        if report.errors and report.mapped == 0:
            return RedirectResponse(
                f"/admin?error={quote('Import LUB: ' + '; '.join(report.errors)[:240])}",
                status_code=303,
            )
        conn.commit()
        return RedirectResponse(
            f"/admin?success={quote(report.as_text())}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(
            f"/admin?error={quote('Błąd importu LUB: ' + str(exc)[:200])}",
            status_code=303,
        )
