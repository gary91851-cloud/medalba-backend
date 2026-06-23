from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ..auth import get_current_provider
from ..db import get_db
from ..extraction_service import extract_from_pdf
from ..guide_service import generate_guide, approve_guide
from ..loop_service import open_loop, advance_loop, get_loop_for_guide
from ..severity import classify_severity
from ..config import get_settings

router = APIRouter(prefix="/api")


# ---------- me ----------
@router.get("/me")
def me(provider=Depends(get_current_provider)):
    db = get_db()
    practice = (
        db.table("practices").select("*").eq("id", provider["practice_id"]).single().execute().data
    )
    return {"provider": provider, "practice": practice}


# ---------- extraction: the zero-typing intake ----------
@router.post("/extract")
async def extract(file: UploadFile = File(...), provider=Depends(get_current_provider)):
    if file.content_type not in ("application/pdf",):
        raise HTTPException(400, "Upload a PDF lab report")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(400, "PDF too large (20MB max)")
    try:
        return extract_from_pdf(pdf_bytes)
    except Exception:
        raise HTTPException(
            422,
            "Couldn't read that PDF automatically. Enter the key values manually below.",
        )


# ---------- NPI registry lookup (public: runs during signup, before auth) ----------
def _npi_query(params: dict) -> list:
    import httpx
    try:
        r = httpx.get(
            "https://npiregistry.cms.hhs.gov/api/",
            params={"version": "2.1", **params},
            timeout=8,
        )
        return r.json().get("results", []) or []
    except Exception:
        return []


def _npi_location(item: dict) -> dict:
    return next(
        (a for a in item.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {}
    ) or {}


@router.get("/npi/search")
def npi_search(q: str):
    q = q.strip()
    if len(q) < 3:
        return []

    out, seen = [], set()

    # Organizations (group practices, clinics)
    for item in _npi_query({"organization_name": q + "*", "enumeration_type": "NPI-2", "limit": 6}):
        basic = item.get("basic", {}) or {}
        loc = _npi_location(item)
        npi = str(item.get("number", ""))
        if npi in seen:
            continue
        seen.add(npi)
        out.append(
            {
                "npi": npi,
                "name": (basic.get("organization_name") or "").title(),
                "kind": "Practice",
                "address": (loc.get("address_1") or "").title(),
                "city": (loc.get("city") or "").title(),
                "state": loc.get("state") or "",
            }
        )

    # Individual providers (solo doctors are often registered this way, not as organizations)
    for item in _npi_query({"last_name": q + "*", "enumeration_type": "NPI-1", "limit": 5}):
        basic = item.get("basic", {}) or {}
        loc = _npi_location(item)
        npi = str(item.get("number", ""))
        if npi in seen:
            continue
        seen.add(npi)
        first = (basic.get("first_name") or "").title()
        last = (basic.get("last_name") or "").title()
        cred = basic.get("credential") or ""
        display = f"{first} {last}" + (f", {cred}" if cred else "")
        out.append(
            {
                "npi": npi,
                "name": display.strip(),
                "kind": "Provider",
                "address": (loc.get("address_1") or "").title(),
                "city": (loc.get("city") or "").title(),
                "state": loc.get("state") or "",
            }
        )

    return out[:10]


# ---------- templates ----------
@router.get("/templates")
def list_templates(provider=Depends(get_current_provider)):
    db = get_db()
    return (
        db.table("templates")
        .select("id, condition, name, age_min, age_max, created_at")
        .eq("practice_id", provider["practice_id"])
        .order("created_at", desc=True)
        .execute()
        .data
    )


# ---------- settings ----------
class PracticeUpdate(BaseModel):
    name: str | None = None
    signoffs: list[str] | None = None


@router.patch("/practice")
def update_practice(body: PracticeUpdate, provider=Depends(get_current_provider)):
    db = get_db()
    updates = {}
    if body.name and body.name.strip():
        updates["name"] = body.name.strip()
    if body.signoffs is not None:
        updates["signoffs"] = [s.strip() for s in body.signoffs if s and s.strip()][:3]
    if updates:
        db.table("practices").update(updates).eq("id", provider["practice_id"]).execute()
    return {"ok": True}


class ProviderUpdate(BaseModel):
    full_name: str | None = None


@router.patch("/me")
def update_me(body: ProviderUpdate, provider=Depends(get_current_provider)):
    db = get_db()
    if body.full_name and body.full_name.strip():
        db.table("providers").update({"full_name": body.full_name.strip()}).eq("id", provider["id"]).execute()
    return {"ok": True}


# ---------- patients ----------
class PatientIn(BaseModel):
    first_name: str
    last_initial: str = ""
    chart_ref: str = ""
    age: int
    conditions: list[str] = []


@router.get("/patients")
def list_patients(provider=Depends(get_current_provider)):
    db = get_db()
    patients = (
        db.table("patients")
        .select("*, guides(id, status, condition, created_at, approved_at, secure_token, feedback, checkoffs)")
        .eq("practice_id", provider["practice_id"])
        .order("created_at", desc=True)
        .execute()
    )
    return patients.data


# ---------- guides ----------
class GuideCreate(BaseModel):
    patient: PatientIn
    condition: str
    values: dict = {}
    prior_values: dict = {}
    medications: list[str] = []
    dietary_notes: str = ""
    clinical_rails: dict = {}  # {priority, constraints, secondary_goals}


@router.post("/guides")
def create_guide(
    body: GuideCreate, background: BackgroundTasks, provider=Depends(get_current_provider)
):
    db = get_db()
    patient = (
        db.table("patients")
        .insert(
            {
                "practice_id": provider["practice_id"],
                "provider_id": provider["id"],
                "first_name": body.patient.first_name.strip(),
                "last_initial": (body.patient.last_initial.strip()[:1].upper() or None) if body.patient.last_initial.strip() else None,
                "chart_ref": body.patient.chart_ref.strip() or None,
                "age": body.patient.age,
                "conditions": body.patient.conditions or [body.condition],
            }
        )
        .execute()
        .data[0]
    )
    guide = (
        db.table("guides")
        .insert(
            {
                "patient_id": patient["id"],
                "practice_id": provider["practice_id"],
                "provider_id": provider["id"],
                "condition": body.condition.strip().lower(),
                "input_data": {
                    "values": body.values,
                    "prior_values": body.prior_values,
                    "medications": body.medications,
                    "dietary_notes": body.dietary_notes,
                },
                "clinical_rails": body.clinical_rails,
                "status": "generating",
            }
        )
        .execute()
        .data[0]
    )
    # Open the closed-loop tracker for this result (status='resulted') before generation fires.
    open_loop(
        guide_id=guide["id"],
        patient_id=patient["id"],
        practice_id=provider["practice_id"],
        provider_id=provider["id"],
        result_label=body.condition.strip(),
        severity=classify_severity(body.values),
    )
    background.add_task(generate_guide, guide["id"])
    return {"guide_id": guide["id"], "patient_id": patient["id"]}


@router.get("/guides/{guide_id}")
def get_guide(guide_id: str, provider=Depends(get_current_provider)):
    db = get_db()
    res = (
        db.table("guides")
        .select("*, patients(first_name, last_initial, chart_ref, age, conditions)")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Guide not found")
    return res.data


class SectionEdit(BaseModel):
    section: str
    content: dict | list


@router.patch("/guides/{guide_id}/section")
def edit_section(guide_id: str, body: SectionEdit, provider=Depends(get_current_provider)):
    db = get_db()
    guide = (
        db.table("guides")
        .select("edited_json, status")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] == "approved":
        raise HTTPException(400, "Guide already approved")
    edited = guide["edited_json"] or {}
    edited[body.section] = body.content
    db.table("guides").update({"edited_json": edited}).eq("id", guide_id).execute()
    return {"ok": True}


class ApproveBody(BaseModel):
    # Follow-up capture (step 3). Defaults make this safe for the current
    # frontend, which sends no body yet — the loop just advances with no action set.
    action_type: str | None = None      # none | repeat_test | referral | appointment
    action_due_date: str | None = None  # ISO date string; required when action_type isn't 'none'
    action_notes: str | None = None


@router.post("/guides/{guide_id}/approve")
def approve(guide_id: str, body: ApproveBody = ApproveBody(), provider=Depends(get_current_provider)):
    db = get_db()
    guide = (
        db.table("guides")
        .select("id, status, conflicts, acknowledged_flags")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] != "pending_review":
        raise HTTPException(400, f"Guide is {guide['status']}, not pending review")
    # Gate: every blocking flag (must_address / should_consider) must be acknowledged
    acked = set(guide.get("acknowledged_flags") or [])
    blocking = [
        i for i, c in enumerate(guide.get("conflicts") or [])
        if (c.get("severity") or "should_consider") in ("must_address", "should_consider")
    ]
    open_flags = [i for i in blocking if i not in acked]
    if open_flags:
        raise HTTPException(
            400,
            f"{len(open_flags)} flag(s) still need to be resolved or acknowledged before approving.",
        )
    token = approve_guide(guide_id, provider["id"])
    # Advance the loop to 'reviewed' and capture the follow-up action the doctor chose.
    advance_loop(
        guide_id, "reviewed", actor="provider", actor_id=provider["id"],
        action_type=body.action_type, action_due_at=body.action_due_date,
        action_note=body.action_notes,
    )
    s = get_settings()
    return {"ok": True, "patient_link": f"{s.guide_base_url}/{token}"}


# ---------- closed-loop read (for the loop detail / timeline view) ----------
@router.get("/guides/{guide_id}/loop")
def read_loop(guide_id: str, provider=Depends(get_current_provider)):
    db = get_db()
    # Ownership check: only return the loop if this provider's practice owns the guide.
    guide = (
        db.table("guides")
        .select("id")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    result = get_loop_for_guide(guide_id)
    if not result:
        return {"loop": None, "events": []}
    return result


# ---------- results board: all loops for the practice ----------
@router.get("/loops")
def list_loops(provider=Depends(get_current_provider)):
    db = get_db()
    return (
        db.table("loops")
        .select(
            "id, guide_id, status, severity, action_type, action_due_at, action_note, "
            "action_completed_at, result_label, resulted_at, reviewed_at, sent_at, "
            "acknowledged_at, closed_at, closed_method, created_at, "
            "patients(first_name, last_initial, age), guides(condition)"
        )
        .eq("practice_id", provider["practice_id"])
        .order("created_at", desc=True)
        .execute()
        .data
    )


class TemplateSave(BaseModel):
    name: str
    age_min: int | None = None
    age_max: int | None = None


@router.post("/guides/{guide_id}/save-template")
def save_template(guide_id: str, body: TemplateSave, provider=Depends(get_current_provider)):
    db = get_db()
    guide = (
        db.table("guides")
        .select("condition, edited_json")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide or not guide["edited_json"]:
        raise HTTPException(404, "Guide not found")
    db.table("templates").insert(
        {
            "practice_id": provider["practice_id"],
            "provider_id": provider["id"],
            "condition": guide["condition"],
            "name": body.name,
            "guide_json": guide["edited_json"],
            "age_min": body.age_min,
            "age_max": body.age_max,
        }
    ).execute()
    return {"ok": True}


@router.post("/guides/{guide_id}/retry")
def retry_guide(guide_id: str, background: BackgroundTasks, provider=Depends(get_current_provider)):
    db = get_db()
    guide = (
        db.table("guides")
        .select("id, status")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] != "failed":
        raise HTTPException(400, "Only failed Guides can be retried")
    db.table("guides").update({"status": "generating", "generated_json": None}).eq("id", guide_id).execute()
    background.add_task(generate_guide, guide_id)
    return {"ok": True}


class SendGuideBody(BaseModel):
    email: str


@router.post("/guides/{guide_id}/send")
def send_guide_to_patient(guide_id: str, body: SendGuideBody, provider=Depends(get_current_provider)):
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or " " in email:
        raise HTTPException(400, "That doesn't look like a valid email address")
    db = get_db()
    guide = (
        db.table("guides")
        .select("id, status, secure_token, condition, patients(first_name)")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] != "approved" or not guide["secure_token"]:
        raise HTTPException(400, "Approve the Guide before sending it")

    practice = (
        db.table("practices").select("name").eq("id", provider["practice_id"]).single().execute().data
    )
    s = get_settings()
    link = f"{s.guide_base_url}/{guide['secure_token']}"

    from ..email_service import send_guide
    ok, reason = send_guide(email, guide["patients"]["first_name"], practice["name"], guide["condition"], link)
    if not ok:
        raise HTTPException(502, reason or "Email couldn't be sent — copy the link and send it yourself.")
    db.table("guides").update({"patient_email": email, "sent_at": "now()"}).eq("id", guide_id).execute()
    # Advance the loop to 'sent' once the email has actually gone out.
    advance_loop(guide_id, "sent", actor="provider", actor_id=provider["id"],
                 metadata={"sent_to": email})
    return {"ok": True, "sent_to": email}


class RegenSection(BaseModel):
    section: str
    instruction: str


@router.post("/guides/{guide_id}/regenerate-section")
def regenerate_section_route(guide_id: str, body: RegenSection, provider=Depends(get_current_provider)):
    if not body.instruction.strip():
        raise HTTPException(400, "Tell us the approach you'd like instead.")
    db = get_db()
    guide = (
        db.table("guides").select("id, status").eq("id", guide_id)
        .eq("practice_id", provider["practice_id"]).single().execute().data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] == "approved":
        raise HTTPException(400, "Guide already approved")
    from ..guide_service import regenerate_section
    try:
        regenerate_section(guide_id, body.section, body.instruction.strip())
    except Exception as e:
        raise HTTPException(502, f"Couldn't revise that section: {e}")
    return {"ok": True}


class AckBody(BaseModel):
    flag_index: int
    acknowledged: bool = True


@router.post("/guides/{guide_id}/acknowledge")
def acknowledge_flag(guide_id: str, body: AckBody, provider=Depends(get_current_provider)):
    db = get_db()
    guide = (
        db.table("guides")
        .select("acknowledged_flags, status")
        .eq("id", guide_id)
        .eq("practice_id", provider["practice_id"])
        .single()
        .execute()
        .data
    )
    if not guide:
        raise HTTPException(404, "Guide not found")
    if guide["status"] == "approved":
        raise HTTPException(400, "Guide already approved")
    acked = set(guide.get("acknowledged_flags") or [])
    if body.acknowledged:
        acked.add(body.flag_index)
    else:
        acked.discard(body.flag_index)
    db.table("guides").update({"acknowledged_flags": sorted(acked)}).eq("id", guide_id).execute()
    return {"ok": True, "acknowledged_flags": sorted(acked)}


# ---------- public patient access (token, no login) ----------
@router.get("/public/guide/{token}")
def public_guide(token: str):
    db = get_db()
    res = (
        db.table("guides")
        .select("id, edited_json, condition, approved_at, checkoffs, practice_id, patients(first_name)")
        .eq("secure_token", token)
        .eq("status", "approved")
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Guide not found")
    g = res.data[0]
    practice = (
        db.table("practices").select("name, signoffs").eq("id", g["practice_id"]).single().execute().data
    )
    signoffs = (practice or {}).get("signoffs") or []
    signoff = ""
    if signoffs:
        # stable per guide, varies across guides
        signoff = signoffs[sum(ord(c) for c in g["id"]) % len(signoffs)]
    return {
        "first_name": g["patients"]["first_name"],
        "condition": g["condition"],
        "approved_at": g["approved_at"],
        "guide": g["edited_json"],
        "checkoffs": g["checkoffs"],
        "practice_name": (practice or {}).get("name") or "",
        "signoff": signoff,
    }


@router.get("/public/guide/{token}/pdf")
def download_guide_pdf(token: str):
    db = get_db()
    res = (
        db.table("guides")
        .select("id, edited_json, condition, approved_at, checkoffs, practice_id, patients(first_name)")
        .eq("secure_token", token)
        .eq("status", "approved")
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Guide not found")
    g = res.data[0]
    practice = (
        db.table("practices").select("name, signoffs").eq("id", g["practice_id"]).single().execute().data
    )
    signoffs = (practice or {}).get("signoffs") or []
    signoff = ""
    if signoffs:
        signoff = signoffs[sum(ord(c) for c in g["id"]) % len(signoffs)]

    from ..pdf_service import generate_guide_pdf
    from io import BytesIO
    pdf_bytes = generate_guide_pdf({
        "first_name": g["patients"]["first_name"],
        "condition": g["condition"],
        "approved_at": g["approved_at"],
        "guide": g["edited_json"],
        "checkoffs": g["checkoffs"],
        "practice_name": (practice or {}).get("name") or "",
        "signoff": signoff,
    })
    safe_name = g["patients"]["first_name"].replace(" ", "_")
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="MedAlba_Guide_{safe_name}.pdf"'},
    )


class Feedback(BaseModel):
    helpful: bool
    section: str | None = None


@router.post("/public/guide/{token}/feedback")
def guide_feedback(token: str, body: Feedback):
    db = get_db()
    res = db.table("guides").select("id, feedback").eq("secure_token", token).execute()
    if not res.data:
        raise HTTPException(404, "Guide not found")
    g = res.data[0]
    feedback = g["feedback"] or []
    feedback.append({"helpful": body.helpful, "section": body.section})
    db.table("guides").update({"feedback": feedback}).eq("id", g["id"]).execute()
    return {"ok": True}


class Checkoff(BaseModel):
    day: int
    done: bool


@router.post("/public/guide/{token}/checkoff")
def guide_checkoff(token: str, body: Checkoff):
    db = get_db()
    res = db.table("guides").select("id, checkoffs").eq("secure_token", token).execute()
    if not res.data:
        raise HTTPException(404, "Guide not found")
    g = res.data[0]
    checkoffs = g["checkoffs"] or {}
    checkoffs[f"day_{body.day}"] = body.done
    db.table("guides").update({"checkoffs": checkoffs}).eq("id", g["id"]).execute()
    return {"ok": True}
