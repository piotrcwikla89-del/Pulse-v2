"""Mostek między color_jobs a silnikiem podobieństwa."""

from __future__ import annotations

from pdf_extractor_core.similarity import ColorSimilarityEngine


def suggest_similar(cur, color_job_id: int, *, limit: int = 12) -> list[dict]:
    cur.execute("SELECT id, order_number FROM color_jobs ORDER BY id DESC LIMIT 500")
    jobs = cur.fetchall()
    specs: list[tuple[str, list[str]]] = []
    id_by_key: dict[str, int] = {}
    for job in jobs:
        job_id = job["id"] if hasattr(job, "keys") else job[0]
        order_number = job["order_number"] if hasattr(job, "keys") else job[1]
        cur.execute(
            "SELECT color_name FROM color_separations WHERE color_job_id=? ORDER BY sort_order, id",
            (job_id,),
        )
        names = [
            (r["color_name"] if hasattr(r, "keys") else r[0])
            for r in cur.fetchall()
        ]
        if not names:
            continue
        key = f"{order_number or job_id}|{job_id}"
        specs.append((key, names))
        id_by_key[key] = int(job_id)

    target_key = next((k for k, jid in id_by_key.items() if jid == int(color_job_id)), None)
    if target_key is None or len(specs) < 2:
        return []

    engine = ColorSimilarityEngine.from_color_lists(specs)
    ranked = engine.most_similar(target_key, limit=limit)
    out: list[dict] = []
    for item in ranked:
        other_key = item.right_order_id
        job_pk = id_by_key.get(other_key)
        if job_pk is None:
            continue
        cur.execute("SELECT * FROM color_jobs WHERE id=?", (job_pk,))
        job = cur.fetchone()
        if not job:
            continue
        out.append(
            {
                "job": job,
                "score": float(item.weighted_jaccard_percent),
                "jaccard": float(item.jaccard_percent),
                "shared": list(item.shared_colors),
                "label": other_key,
            }
        )
    return out
