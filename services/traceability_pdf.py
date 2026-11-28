"""PDF dossier z wyniku wyszukiwania traceability."""

from __future__ import annotations

import io
from pathlib import Path

DEJAVU_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _lub(value) -> str:
    from helpers import format_lub_label

    return format_lub_label(value) if value else "—"


def render_traceability_pdf(context: dict, generated_by: str = "") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = "helv"
    font_bold = "helv"
    if DEJAVU_REGULAR.exists():
        font = "dejavu"
        page.insert_font(fontname=font, fontfile=str(DEJAVU_REGULAR))
    if DEJAVU_BOLD.exists():
        font_bold = "dejavub"
        page.insert_font(fontname=font_bold, fontfile=str(DEJAVU_BOLD))

    y = 40
    left = 40

    def ensure_page():
        nonlocal y, page
        if y <= 800:
            return
        page = doc.new_page(width=595, height=842)
        if DEJAVU_REGULAR.exists():
            page.insert_font(fontname=font, fontfile=str(DEJAVU_REGULAR))
        if DEJAVU_BOLD.exists():
            page.insert_font(fontname=font_bold, fontfile=str(DEJAVU_BOLD))
        y = 40

    def write(text: str, size=10, bold=False, color=(0, 0, 0)):
        nonlocal y
        ensure_page()
        page.insert_text(
            (left, y),
            str(text),
            fontsize=size,
            fontname=font_bold if bold else font,
            color=color,
        )
        y += size + 5

    keys = context.get("keys") or {}
    write("Traceability — dossier", size=16, bold=True)
    write(f"Zapytanie: {context.get('query') or '—'}", size=11)
    write(
        f"Tryb: {context.get('match_mode') or '—'}  ·  "
        f"zleceń: {context.get('plans_count') or 0}  ·  "
        f"LUB {_lub(keys.get('lub_number'))}  ·  artwork {keys.get('artwork_number') or '—'}"
    )
    if generated_by:
        write(f"Wygenerował: {generated_by}", size=9, color=(0.35, 0.35, 0.35))
    write("")

    write("Historyczne zlecenia", size=13, bold=True)
    for plan in context.get("plans") or []:
        write(
            f"{plan.get('machine')} · {plan.get('order_number')} · {plan.get('status')} · "
            f"art {plan.get('artwork_number') or '—'} · LUB {_lub(plan.get('lub_number'))}",
            size=9,
            bold=True,
        )
        rolls = plan.get("roll_totals") or {}
        write(
            f"  zadruk: {plan.get('print_day') or '—'}  ·  "
            f"{rolls.get('meters', 0)} m / {rolls.get('pieces', 0)} szt. ({rolls.get('roll_count', 0)} rolek)",
            size=8,
        )
        mats = plan.get("materials_at_print") or []
        if mats:
            write("  Farby na dzień zadruku:", size=8)
            for m in mats:
                write(
                    f"    {m.get('pantone')} · prod. {m.get('data_produkcji_label') or '—'} · "
                    f"wiek {m.get('age_days_on_print') if m.get('age_days_on_print') is not None else '—'} dni · "
                    f"{m.get('alert_on_print') or 'ok'}",
                    size=8,
                )
        y += 2

    write("")
    plan = context.get("plan")
    if plan:
        write(f"Dossier: {plan.get('order_number')}", size=13, bold=True)
        write(
            f"Maszyna {plan.get('machine')} · status {plan.get('status')} · "
            f"dzień zadruku {plan.get('print_day') or '—'}"
        )
        mats = plan.get("materials_at_print") or context.get("materials") or []
        write("Farby (data produkcji → wiek na dzień zadruku)", size=11, bold=True)
        if mats:
            for m in mats:
                write(
                    f"  {m.get('pantone')} · LUB {_lub(m.get('lub'))} · półka {m.get('polka') or '—'} · "
                    f"prod. {m.get('data_produkcji_label') or m.get('data_produkcji') or '—'} · "
                    f"wiek {m.get('age_days_on_print') if m.get('age_days_on_print') is not None else '—'} dni · "
                    f"alert {m.get('alert_on_print') or m.get('mag_alert') or 'ok'}",
                    size=8,
                )
        else:
            write("  Brak farb", size=8, color=(0.4, 0.4, 0.4))

        write("Polimery", size=11, bold=True)
        pols = context.get("polimery") or []
        if pols:
            for p in pols:
                write(
                    f"  {p.get('kolor')} · LUB {_lub(p.get('lub'))} · {p.get('lokalizacja') or '—'} · {p.get('status')}",
                    size=8,
                )
        else:
            write("  Brak polimerów", size=8, color=(0.4, 0.4, 0.4))

        write("Kontrola zadruku", size=11, bold=True)
        for r in (context.get("print_reports") or [])[:40]:
            write(
                f"  rolka {r.get('roll_no') or '—'} · {r.get('status')} · "
                f"{r.get('roll_meters') or 0} m / {r.get('roll_pieces') or 0} szt. · "
                f"wymiary {r.get('dimensions_match') or '—'} · kolory {r.get('colors_match') or '—'} · "
                f"kody {r.get('barcode_grade') or '—'}",
                size=8,
            )

        write("Raporty produkcji", size=11, bold=True)
        for r in (context.get("reports") or [])[:20]:
            write(
                f"  {r.get('job_number')} · qty {r.get('quantity')} · OK {r.get('ok_quantity')} · NOK {r.get('nok_quantity')}",
                size=8,
            )
    else:
        write("Farby / polimery (klasyfikacja)", size=13, bold=True)
        for m in context.get("materials") or []:
            write(
                f"  {m.get('pantone')} · prod. {m.get('data_produkcji_label') or m.get('data_produkcji') or '—'} · "
                f"alert {m.get('alert_on_print') or m.get('mag_alert') or 'ok'}",
                size=8,
            )
        for p in context.get("polimery") or []:
            write(f"  polimer {p.get('kolor')} · {_lub(p.get('lub'))} · {p.get('status')}", size=8)

    write("")
    write("Zdarzenia (skrót)", size=11, bold=True)
    for ev in (context.get("events") or [])[:30]:
        write(
            f"  {ev.get('created_at')} · {ev.get('event_type')} · {ev.get('actor_user')} · plan #{ev.get('plan_id') or '—'}",
            size=8,
        )

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()
