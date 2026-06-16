"""Generate a branded, downloadable PDF from an approved Guide.

Uses reportlab for reliable PDF generation. Mirrors the patient Guide's
visual structure: header, values, body impact, 7 days, meds, timeline, sign-off.
"""
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.colors import HexColor

TEAL = HexColor("#2e6e6a")
TEAL_DEEP = HexColor("#235753")
TEAL_WASH = HexColor("#edf3f1")
INK = HexColor("#1d3a40")
INK_SOFT = HexColor("#49616a")
SAGE = HexColor("#7fa886")
SAGE_WASH = HexColor("#eef4ec")
RED = HexColor("#a8443a")
RED_BG = HexColor("#f9ece9")
AMBER = HexColor("#b97c2b")
AMBER_BG = HexColor("#fdf3e3")
LINE = HexColor("#e6e0d4")
IVORY = HexColor("#f6f3ea")

LEVEL_COLORS = {
    "high": (RED, RED_BG),
    "borderline": (AMBER, AMBER_BG),
    "normal": (SAGE, SAGE_WASH),
    "low": (SAGE, SAGE_WASH),
}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Brand", fontName="Helvetica-Bold", fontSize=18, textColor=TEAL, alignment=TA_CENTER, spaceAfter=2))
    ss.add(ParagraphStyle("Tagline", fontName="Helvetica-Oblique", fontSize=9, textColor=INK_SOFT, alignment=TA_CENTER, spaceAfter=14))
    ss.add(ParagraphStyle("Hero", fontName="Helvetica-Bold", fontSize=16, textColor=INK, alignment=TA_CENTER, leading=20, spaceAfter=4))
    ss.add(ParagraphStyle("Approved", fontName="Helvetica", fontSize=8.5, textColor=INK_SOFT, alignment=TA_CENTER, spaceAfter=16))
    ss.add(ParagraphStyle("SectionHead", fontName="Helvetica-Bold", fontSize=12, textColor=TEAL_DEEP, spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle("Body", fontName="Helvetica", fontSize=10, textColor=INK, leading=14.5, spaceAfter=6))
    ss.add(ParagraphStyle("BodyBold", fontName="Helvetica-Bold", fontSize=10, textColor=INK, leading=14.5, spaceAfter=6))
    ss.add(ParagraphStyle("Small", fontName="Helvetica", fontSize=8.5, textColor=INK_SOFT, leading=12, spaceAfter=4))
    ss.add(ParagraphStyle("SmallBold", fontName="Helvetica-Bold", fontSize=8.5, textColor=INK_SOFT, leading=12, spaceAfter=4))
    ss.add(ParagraphStyle("ValueName", fontName="Helvetica-Bold", fontSize=8, textColor=INK_SOFT, leading=10))
    ss.add(ParagraphStyle("ValueNum", fontName="Helvetica-Bold", fontSize=16, textColor=INK, leading=18))
    ss.add(ParagraphStyle("DayHead", fontName="Helvetica-Bold", fontSize=10.5, textColor=TEAL_DEEP, spaceAfter=4, spaceBefore=10))
    ss.add(ParagraphStyle("MealLabel", fontName="Helvetica-Bold", fontSize=8, textColor=TEAL, leading=12))
    ss.add(ParagraphStyle("MealText", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13))
    ss.add(ParagraphStyle("Signoff", fontName="Helvetica-Oblique", fontSize=10.5, textColor=TEAL_DEEP, alignment=TA_CENTER, leading=14, spaceBefore=12, spaceAfter=2))
    ss.add(ParagraphStyle("SignoffPractice", fontName="Helvetica-Bold", fontSize=9, textColor=INK_SOFT, alignment=TA_CENTER, spaceAfter=14))
    ss.add(ParagraphStyle("Disclaimer", fontName="Helvetica", fontSize=7, textColor=INK_SOFT, alignment=TA_CENTER, leading=10, spaceBefore=10))
    ss.add(ParagraphStyle("Milestone", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13, spaceAfter=3))
    ss.add(ParagraphStyle("TrendNote", fontName="Helvetica-Bold", fontSize=10, textColor=HexColor("#2f5237"), leading=14, spaceAfter=8))
    return ss


def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=LINE, spaceAfter=8, spaceBefore=8)


def _value_table(values, ss):
    if not values:
        return []
    rows = []
    for v in values:
        level = v.get("level", "normal")
        fg, bg = LEVEL_COLORS.get(level, (SAGE, SAGE_WASH))
        rows.append([
            Paragraph(f"{v.get('name','')}", ss["ValueName"]),
            Paragraph(f"<b>{v.get('value','')}</b> {v.get('unit','')}", ss["ValueNum"]),
            Paragraph(f"{level.upper()}", ParagraphStyle("Lvl", fontName="Helvetica-Bold", fontSize=7, textColor=fg, alignment=TA_CENTER)),
        ])
    t = Table(rows, colWidths=[2.2*inch, 2.5*inch, 1*inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW", (0,0), (-1,-2), 0.5, LINE),
    ]))
    return [t, Spacer(1, 6)]


def generate_guide_pdf(guide_data: dict) -> bytes:
    g = guide_data.get("guide") or {}
    first_name = guide_data.get("first_name", "")
    condition = guide_data.get("condition", "")
    approved_at = guide_data.get("approved_at", "")
    signoff = guide_data.get("signoff", "")
    practice_name = guide_data.get("practice_name", "")

    ss = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.6*inch, bottomMargin=0.6*inch)
    story = []

    # Header
    story.append(Paragraph("MedAlba", ss["Brand"]))
    story.append(Paragraph("With you.", ss["Tagline"]))
    story.append(Paragraph(
        f"{first_name}, here's what your results mean — and what to do next.",
        ss["Hero"]))
    approved_str = ""
    if approved_at:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            approved_str = dt.strftime("%B %d, %Y")
        except Exception:
            approved_str = str(approved_at)[:10]
    story.append(Paragraph(
        f"Reviewed and approved by your doctor{' on ' + approved_str if approved_str else ''}. "
        "Your doctor made every clinical decision in this Guide.", ss["Approved"]))
    story.append(_hr())

    # Trend callout
    trend = (g.get("progress_timeline") or {}).get("trend_note", "")
    if trend:
        story.append(Paragraph(f"↘ {trend}", ss["TrendNote"]))
        story.append(Spacer(1, 4))

    # Results decoded
    rd = g.get("results_decoded")
    if rd:
        story.append(Paragraph("Your results, in plain English", ss["SectionHead"]))
        if rd.get("summary"):
            story.append(Paragraph(rd["summary"], ss["Body"]))
        story.extend(_value_table(rd.get("values", []), ss))
        for v in rd.get("values", []):
            if v.get("meaning"):
                story.append(Paragraph(f"<b>{v.get('name','')}</b>: {v['meaning']}", ss["Small"]))
        story.append(_hr())

    # Body impact
    bi = g.get("body_impact")
    if bi:
        story.append(Paragraph("What's happening in your body", ss["SectionHead"]))
        if bi.get("summary"):
            story.append(Paragraph(bi["summary"], ss["Body"]))
        for o in bi.get("organs", []):
            story.append(Paragraph(f"<b>{o.get('organ','')}</b>: {o.get('effect','')}", ss["Body"]))
        if bi.get("connection"):
            story.append(Paragraph(bi["connection"], ParagraphStyle(
                "Conn", fontName="Helvetica-Oblique", fontSize=9.5, textColor=TEAL_DEEP, leading=13, spaceAfter=6)))
        story.append(_hr())

    # Daily guidance
    dg = g.get("daily_guidance")
    if dg:
        story.append(Paragraph("Your next 7 days", ss["SectionHead"]))
        if dg.get("overview"):
            story.append(Paragraph(dg["overview"], ss["Body"]))
        for d in dg.get("days", []):
            day_items = [Paragraph(f"Day {d.get('day','')}", ss["DayHead"])]
            meals = d.get("meals") or {}
            for m in ["breakfast", "lunch", "dinner", "snack"]:
                if meals.get(m):
                    day_items.append(Paragraph(f"<b>{m.upper()}</b>  {meals[m]}", ss["MealText"]))
            if d.get("movement"):
                day_items.append(Paragraph(f"<b>MOVE</b>  {d['movement']}", ss["MealText"]))
            if d.get("habit"):
                day_items.append(Paragraph(f"<b>HABIT</b>  {d['habit']}", ss["MealText"]))
            story.append(KeepTogether(day_items))

        if dg.get("grocery_list"):
            story.append(Paragraph("Starter shopping suggestions", ss["DayHead"]))
            for item in dg["grocery_list"]:
                story.append(Paragraph(f"• {item}", ss["Small"]))

        if dg.get("substitutions"):
            story.append(Paragraph("Easy swaps", ss["DayHead"]))
            for s in dg["substitutions"]:
                story.append(Paragraph(
                    f"<strike>{s.get('instead_of','')}</strike>  →  <b>{s.get('use','')}</b>"
                    + (f"  ({s['why']})" if s.get("why") else ""),
                    ss["Small"]))
        story.append(_hr())

    # Medications
    meds = g.get("medication_guide")
    if meds and len(meds) > 0:
        story.append(Paragraph("Your medications, explained", ss["SectionHead"]))
        for m in meds:
            med_items = [Paragraph(f"<b>{m.get('name','')}</b>", ss["BodyBold"])]
            for field, label in [("what_it_does","What it does"), ("why_you_take_it","Why you take it"), ("what_to_expect","What to expect")]:
                if m.get(field):
                    med_items.append(Paragraph(f"<b>{label}:</b> {m[field]}", ss["Small"]))
            if m.get("common_questions"):
                med_items.append(Paragraph(m["common_questions"], ParagraphStyle(
                    "MedQ", fontName="Helvetica", fontSize=8.5, textColor=TEAL_DEEP, leading=12, spaceAfter=8)))
            story.append(KeepTogether(med_items))
        story.append(_hr())

    # Holistic
    hol = g.get("holistic_options")
    if hol and len(hol) > 0:
        story.append(Paragraph("Companions your doctor approved", ss["SectionHead"]))
        for h in hol:
            story.append(Paragraph(f"<b>{h.get('option','')}</b>", ss["BodyBold"]))
            if h.get("how"):
                story.append(Paragraph(h["how"], ss["Small"]))
            if h.get("evidence"):
                story.append(Paragraph(h["evidence"], ParagraphStyle(
                    "Ev", fontName="Helvetica-Oblique", fontSize=8, textColor=INK_SOFT, leading=11, spaceAfter=6)))
        story.append(_hr())

    # Reassurance (3 AM questions)
    reas = g.get("reassurance")
    if reas and len(reas) > 0:
        story.append(Paragraph("Questions you might have", ss["SectionHead"]))
        for r in reas:
            story.append(Paragraph(f"<i>{r.get('question','')}</i>", ParagraphStyle(
                "Q3am", fontName="Helvetica-BoldOblique", fontSize=10, textColor=INK, leading=14, spaceAfter=2)))
            story.append(Paragraph(r.get("answer",""), ss["Body"]))
        story.append(_hr())

    # Next steps
    ns = g.get("next_steps")
    if ns:
        story.append(Paragraph("What happens next", ss["SectionHead"]))
        if ns.get("recheck_description"):
            story.append(Paragraph(ns["recheck_description"], ss["Body"]))
        if ns.get("recheck_timeframe"):
            story.append(Paragraph(f"<b>Recheck: {ns['recheck_timeframe']}</b>", ss["BodyBold"]))
        for t in ns.get("targets", []):
            story.append(Paragraph(
                f"<b>{t.get('measure','')}</b>: {t.get('goal','')} — {t.get('what_it_means','')}",
                ss["Small"]))
        if ns.get("if_not_improving"):
            story.append(Paragraph(
                f"<b>If numbers haven't moved:</b> {ns['if_not_improving']}", ss["Body"]))
        story.append(_hr())

    # Timeline
    pt = g.get("progress_timeline")
    if pt:
        story.append(Paragraph("Where this is headed", ss["SectionHead"]))
        if pt.get("framing"):
            story.append(Paragraph(pt["framing"], ss["Body"]))
        for m in pt.get("milestones", []):
            story.append(Paragraph(
                f"<b>Week {m.get('week','')}</b>: {m.get('expect','')}",
                ss["Milestone"]))
            if m.get("measurable"):
                story.append(Paragraph(m["measurable"], ss["Small"]))
        story.append(_hr())

    # Sign-off
    if signoff:
        story.append(Spacer(1, 6))
        story.append(Paragraph(signoff, ss["Signoff"]))
        if practice_name:
            story.append(Paragraph(practice_name, ss["SignoffPractice"]))

    # Disclaimer
    story.append(Paragraph(
        "This Guide is educational support reviewed and approved by your doctor. "
        "It is not a substitute for medical advice — for any clinical question, "
        "contact your doctor's office.", ss["Disclaimer"]))
    story.append(Paragraph("MedAlba · With you.", ParagraphStyle(
        "BrandFoot", fontName="Helvetica-Oblique", fontSize=7, textColor=TEAL, alignment=TA_CENTER, spaceBefore=4)))

    doc.build(story)
    return buf.getvalue()
