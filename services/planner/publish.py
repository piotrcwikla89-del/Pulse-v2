"""Publikacja draftu planu — merge z istniejącymi production_plans."""

from __future__ import annotations

from dataclasses import dataclass, field

from helpers import PRODUCTION_MACHINES


@dataclass
class PublishReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return (
            f"dodane={len(self.added)}, zaktualizowane={len(self.updated)}, "
            f"pominięte={len(self.skipped)}, konflikty={len(self.conflicts)}"
        )


def publish_draft(cur, draft_id: int, *, published_by: str) -> PublishReport:
    report = PublishReport()
    cur.execute("SELECT id, status FROM plan_drafts WHERE id=?", (draft_id,))
    draft = cur.fetchone()
    if not draft:
        report.conflicts.append("Brak draftu")
        return report
    status = draft["status"] if hasattr(draft, "keys") else draft[1]
    if status == "published":
        report.skipped.append("Draft już opublikowany")
        return report

    cur.execute(
        """
        SELECT * FROM plan_draft_items
        WHERE draft_id=?
        ORDER BY machine, sort_order, id
        """,
        (draft_id,),
    )
    items = cur.fetchall()

    for item in items:
        machine = (item["machine"] if hasattr(item, "keys") else item[2] or "").upper()
        order_number = (item["order_number"] if hasattr(item, "keys") else item[4]) or ""
        artwork = item["artwork_number"] if hasattr(item, "keys") else item[5]
        lub = item["lub_number"] if hasattr(item, "keys") else item[6]
        order_name = item["order_name"] if hasattr(item, "keys") else item[7]
        color_job_id = item["color_job_id"] if hasattr(item, "keys") else item[3]

        if machine not in PRODUCTION_MACHINES or not order_number:
            report.skipped.append(f"{order_number or '?'} — brak maszyny/nr zlecenia")
            continue

        cur.execute(
            """
            SELECT id, machine, status FROM production_plans
            WHERE order_number=?
            ORDER BY id DESC
            """,
            (order_number,),
        )
        existing_rows = cur.fetchall()
        active = [
            r
            for r in existing_rows
            if (r["status"] if hasattr(r, "keys") else r[2]) in ("planned", "in_progress")
        ]

        if any((r["status"] if hasattr(r, "keys") else r[2]) == "in_progress" for r in active):
            report.skipped.append(f"{order_number} — w toku na maszynie")
            continue

        same_machine = [
            r
            for r in active
            if (r["machine"] if hasattr(r, "keys") else r[1]) == machine
            and (r["status"] if hasattr(r, "keys") else r[2]) == "planned"
        ]
        other_machine = [
            r
            for r in active
            if (r["machine"] if hasattr(r, "keys") else r[1]) != machine
            and (r["status"] if hasattr(r, "keys") else r[2]) == "planned"
        ]

        if other_machine:
            other = other_machine[0]
            other_m = other["machine"] if hasattr(other, "keys") else other[1]
            report.conflicts.append(f"{order_number} już planned na {other_m} — bez auto-przeniesienia")
            continue

        if same_machine:
            plan_id = same_machine[0]["id"] if hasattr(same_machine[0], "keys") else same_machine[0][0]
            cur.execute(
                """
                UPDATE production_plans
                SET artwork_number=COALESCE(?, artwork_number),
                    lub_number=COALESCE(?, lub_number),
                    order_name=COALESCE(?, order_name),
                    color_job_id=COALESCE(?, color_job_id)
                WHERE id=?
                """,
                (artwork, lub, order_name, color_job_id, plan_id),
            )
            report.updated.append(f"{order_number}@{machine}")
            continue

        cur.execute(
            """
            INSERT INTO production_plans
                (machine, order_number, artwork_number, lub_number, order_name,
                 meters, pieces, status, assortment_prep_status, farby_prep_status,
                 polimery_prep_status, color_job_id)
            VALUES (?, ?, ?, ?, ?, 0, 0, 'planned', 'pending', 'pending', 'pending', ?)
            """,
            (machine, order_number, artwork, lub, order_name, color_job_id),
        )
        report.added.append(f"{order_number}@{machine}")

    cur.execute(
        "UPDATE plan_drafts SET status='published', published_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (draft_id,),
    )
    cur.execute(
        "INSERT INTO plan_publish_log (draft_id, published_by, summary) VALUES (?, ?, ?)",
        (draft_id, published_by, report.as_text()),
    )
    return report
