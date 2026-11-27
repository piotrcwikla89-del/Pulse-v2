"""Kolejność kolejek maszyn — draft sort_order + production_plans.sort_order."""

from __future__ import annotations

from helpers import PRODUCTION_MACHINES


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


def ensure_production_plans_sort_order(cur, execute) -> None:
    """Dodaje kolumnę sort_order i uzupełnia kolejność wg dotychczasowego id."""

    from db_compat import is_postgres

    if is_postgres():
        execute(
            cur,
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='production_plans'
              AND column_name='sort_order'
            """,
        )
        if not cur.fetchone():
            execute(cur, "ALTER TABLE production_plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    else:
        execute(cur, "PRAGMA table_info(production_plans)")
        existing = {row[1] for row in cur.fetchall()}
        if "sort_order" not in existing:
            execute(cur, "ALTER TABLE production_plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")

    # Backfill: per maszyna, planned wg id; in_progress → 0
    for machine in PRODUCTION_MACHINES:
        execute(
            cur,
            """
            SELECT id FROM production_plans
            WHERE machine=? AND status='planned'
            ORDER BY sort_order, id
            """,
            (machine,),
        )
        rows = cur.fetchall()
        # Jeśli wszystkie mają 0 i jest >1, uporządkuj po id
        ids = [int(_row_get(r, "id", 0)) for r in rows]
        if not ids:
            continue
        execute(
            cur,
            """
            SELECT COUNT(*) AS c FROM production_plans
            WHERE machine=? AND status='planned' AND sort_order != 0
            """,
            (machine,),
        )
        nonzero = int(_row_get(cur.fetchone(), "c", 0) or 0)
        if nonzero == 0 and len(ids) > 1:
            # pierwszy backfill — kolejność jak dotychczas (id)
            execute(
                cur,
                """
                SELECT id FROM production_plans
                WHERE machine=? AND status='planned'
                ORDER BY id
                """,
                (machine,),
            )
            ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
        for idx, plan_id in enumerate(ids):
            execute(
                cur,
                "UPDATE production_plans SET sort_order=? WHERE id=?",
                (idx, plan_id),
            )
        execute(
            cur,
            """
            UPDATE production_plans SET sort_order=0
            WHERE machine=? AND status='in_progress'
            """,
            (machine,),
        )


def _renumber_live_planned(cur, machine: str, ordered_ids: list[int]) -> None:
    for idx, plan_id in enumerate(ordered_ids):
        cur.execute(
            "UPDATE production_plans SET sort_order=? WHERE id=? AND machine=? AND status='planned'",
            (idx, plan_id, machine),
        )


def insert_job_into_live_queue(
    cur,
    *,
    job_row,
    machine: str,
    before_plan_id: int | None = None,
) -> tuple[dict | None, str | None]:
    """Wstawia color_job jako planned na maszynie we wskazanej pozycji (przed before_plan_id)."""

    machine = machine.upper().strip()
    if machine not in PRODUCTION_MACHINES:
        return None, "Nieprawidłowa maszyna"

    order_number = (_row_get(job_row, "order_number") or "").strip()
    if not order_number:
        return None, "Brak numeru zlecenia"
    job_id = int(_row_get(job_row, "id", 0))

    cur.execute(
        """
        SELECT id, machine, status FROM production_plans
        WHERE order_number=? AND status IN ('planned', 'in_progress')
        ORDER BY id DESC
        """,
        (order_number,),
    )
    active = cur.fetchall()
    for row in active:
        st = _row_get(row, "status", 2)
        m = (_row_get(row, "machine", 1) or "").upper()
        if st == "in_progress":
            return None, f"{order_number} jest w toku na {m}"
        if m != machine and st == "planned":
            return None, f"{order_number} już planned na {m}"
        if m == machine and st == "planned":
            # Już na tej maszynie — tylko przesuń w kolejce
            plan_id = int(_row_get(row, "id", 0))
            cur.execute(
                """
                SELECT id FROM production_plans
                WHERE machine=? AND status='planned'
                ORDER BY sort_order, id
                """,
                (machine,),
            )
            ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
            if plan_id in ids:
                ids.remove(plan_id)
            if before_plan_id and int(before_plan_id) in ids:
                idx = ids.index(int(before_plan_id))
                ids.insert(idx, plan_id)
            else:
                ids.append(plan_id)
            _renumber_live_planned(cur, machine, ids)
            cur.execute(
                """
                UPDATE production_plans
                SET color_job_id=COALESCE(?, color_job_id),
                    order_name=COALESCE(?, order_name),
                    artwork_number=COALESCE(?, artwork_number),
                    lub_number=COALESCE(?, lub_number)
                WHERE id=?
                """,
                (
                    job_id,
                    _row_get(job_row, "order_name"),
                    _row_get(job_row, "artwork_number"),
                    _row_get(job_row, "lub_number"),
                    plan_id,
                ),
            )
            return {
                "plan_id": plan_id,
                "machine": machine,
                "order_number": order_number,
                "moved": True,
            }, None

    cur.execute(
        """
        SELECT id FROM production_plans
        WHERE machine=? AND status='planned'
        ORDER BY sort_order, id
        """,
        (machine,),
    )
    ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]

    cur.execute(
        """
        INSERT INTO production_plans
            (machine, order_number, artwork_number, lub_number, order_name,
             meters, pieces, status, assortment_prep_status, farby_prep_status,
             polimery_prep_status, color_job_id, sort_order)
        VALUES (?, ?, ?, ?, ?, 0, 0, 'planned', 'pending', 'pending', 'pending', ?, 0)
        """,
        (
            machine,
            order_number,
            _row_get(job_row, "artwork_number"),
            _row_get(job_row, "lub_number"),
            _row_get(job_row, "order_name"),
            job_id,
        ),
    )
    plan_id = int(cur.lastrowid)
    if before_plan_id and int(before_plan_id) in ids:
        idx = ids.index(int(before_plan_id))
        ids.insert(idx, plan_id)
    else:
        ids.append(plan_id)
    _renumber_live_planned(cur, machine, ids)
    return {
        "plan_id": plan_id,
        "machine": machine,
        "order_number": order_number,
        "moved": False,
    }, None


def reorder_live_queue(cur, machine: str, ordered_ids: list[int]) -> tuple[bool, str | None]:
    machine = machine.upper().strip()
    if machine not in PRODUCTION_MACHINES:
        return False, "Nieprawidłowa maszyna"
    if not ordered_ids:
        return False, "Pusta kolejność"

    cur.execute(
        """
        SELECT id FROM production_plans
        WHERE machine=? AND status='planned'
        ORDER BY sort_order, id
        """,
        (machine,),
    )
    current = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
    wanted = [int(x) for x in ordered_ids]
    if sorted(current) != sorted(wanted):
        return False, "Lista nie pokrywa się z kolejką maszyny (odśwież stronę)"

    _renumber_live_planned(cur, machine, wanted)
    return True, None


def move_live_plan_to_machine(
    cur,
    *,
    plan_id: int,
    target_machine: str,
    before_plan_id: int | None = None,
) -> tuple[bool, str | None]:
    """Przenosi planned między maszynami D6/D8/D10 z zachowaniem pozycji."""

    target_machine = target_machine.upper().strip()
    if target_machine not in PRODUCTION_MACHINES:
        return False, "Nieprawidłowa maszyna docelowa"

    cur.execute("SELECT id, machine, status FROM production_plans WHERE id=?", (int(plan_id),))
    row = cur.fetchone()
    if not row:
        return False, "Brak pozycji"
    status = _row_get(row, "status", 2) or ""
    if status != "planned":
        return False, "Można przenosić tylko prace planned"
    src_machine = (_row_get(row, "machine", 1) or "").upper()

    # Usuń z kolejki źródłowej
    cur.execute(
        """
        SELECT id FROM production_plans
        WHERE machine=? AND status='planned'
        ORDER BY sort_order, id
        """,
        (src_machine,),
    )
    src_ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
    if int(plan_id) in src_ids:
        src_ids.remove(int(plan_id))
    _renumber_live_planned(cur, src_machine, src_ids)

    cur.execute(
        "UPDATE production_plans SET machine=? WHERE id=? AND status='planned'",
        (target_machine, int(plan_id)),
    )

    cur.execute(
        """
        SELECT id FROM production_plans
        WHERE machine=? AND status='planned' AND id!=?
        ORDER BY sort_order, id
        """,
        (target_machine, int(plan_id)),
    )
    dst_ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
    if before_plan_id and int(before_plan_id) in dst_ids:
        idx = dst_ids.index(int(before_plan_id))
        dst_ids.insert(idx, int(plan_id))
    else:
        dst_ids.append(int(plan_id))
    _renumber_live_planned(cur, target_machine, dst_ids)
    return True, None


def reorder_draft_queue(
    cur,
    *,
    draft_id: int,
    machine: str,
    ordered_ids: list[int],
) -> tuple[bool, str | None]:
    machine = machine.upper().strip()
    if machine not in PRODUCTION_MACHINES:
        return False, "Nieprawidłowa maszyna"
    if not ordered_ids:
        return False, "Pusta kolejność"

    cur.execute(
        """
        SELECT id FROM plan_draft_items
        WHERE draft_id=? AND UPPER(machine)=?
        ORDER BY sort_order, id
        """,
        (draft_id, machine),
    )
    current = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
    wanted = [int(x) for x in ordered_ids]
    if sorted(current) != sorted(wanted):
        return False, "Lista nie pokrywa się z draftem (odśwież stronę)"

    for idx, item_id in enumerate(wanted):
        cur.execute(
            """
            UPDATE plan_draft_items SET sort_order=?
            WHERE id=? AND draft_id=? AND UPPER(machine)=?
            """,
            (idx, item_id, draft_id, machine),
        )
    return True, None


def apply_draft_sort_to_production(cur, draft_id: int) -> None:
    """Przy publish: ustawia production_plans.sort_order wg kolejności draftu (per maszyna)."""

    for machine in PRODUCTION_MACHINES:
        cur.execute(
            """
            SELECT order_number, sort_order FROM plan_draft_items
            WHERE draft_id=? AND UPPER(machine)=?
            ORDER BY sort_order, id
            """,
            (draft_id, machine),
        )
        draft_rows = cur.fetchall()
        if not draft_rows:
            continue

        # Map order_number → draft sort index
        draft_rank: dict[str, int] = {}
        for row in draft_rows:
            onum = (_row_get(row, "order_number", 0) or "").strip()
            if onum and onum not in draft_rank:
                draft_rank[onum] = int(_row_get(row, "sort_order", 1) or 0)

        cur.execute(
            """
            SELECT id, order_number FROM production_plans
            WHERE machine=? AND status='planned'
            ORDER BY sort_order, id
            """,
            (machine,),
        )
        planned = cur.fetchall()

        in_draft: list[tuple[int, int]] = []  # (rank, plan_id)
        outside: list[int] = []
        for plan in planned:
            pid = int(_row_get(plan, "id", 0))
            onum = (_row_get(plan, "order_number", 1) or "").strip()
            if onum in draft_rank:
                in_draft.append((draft_rank[onum], pid))
            else:
                outside.append(pid)

        in_draft.sort(key=lambda t: t[0])
        # Draft order first (planista), then remaining planned jobs in previous relative order
        new_order = [pid for _, pid in in_draft] + outside
        for idx, plan_id in enumerate(new_order):
            cur.execute(
                "UPDATE production_plans SET sort_order=? WHERE id=?",
                (idx, plan_id),
            )
