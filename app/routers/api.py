from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from ..auth import get_current_provider
from ..db import get_db
from ..extraction_service import extract_from_pdf
from ..guide_service import generate_guide, approve_guide
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


# ---------- patients ----------
class PatientIn(BaseModel):
    first_name: str
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
    background.add_task(generate_guide, guide["id"])
    return {"guide_id": guide["id"], "patient_id": patient["id"]}


@router.get("/guides/{guide_id}")
def get_guide(guide_id: str, provider=Depends(get_current_provider)):
    db = get_db()
    res = (
        db.table("guides")
        .select("*, patients(first_name, age, conditions)")
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


@router.post("/guides/{guide_id}/approve")
def approve(guide_id: str, provider=Depends(get_current_provider)):
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
    if guide["status"] != "pending_review":
        raise HTTPException(400, f"Guide is {guide['status']}, not pending review")
    token = approve_guide(guide_id)
    s = get_settings()
    return {"ok": True, "patient_link": f"{s.guide_base_url}/{token}"}


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
    ok = send_guide(email, guide["patients"]["first_name"], practice["name"], guide["condition"], link)
    if not ok:
        raise HTTPException(
            502,
            "Email couldn't be sent. Check the address, or copy the link and send it yourself.",
        )
    db.table("guides").update({"patient_email": email, "sent_at": "now()"}).eq("id", guide_id).execute()
    return {"ok": True, "sent_to": email}


# ---------- public patient access (token, no login) ----------
@router.get("/public/guide/{token}")
def public_guide(token: str):
    db = get_db()
    res = (
        db.table("guides")
        .select("edited_json, condition, approved_at, checkoffs, patients(first_name)")
        .eq("secure_token", token)
        .eq("status", "approved")
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Guide not found")
    g = res.data[0]
    return {
        "first_name": g["patients"]["first_name"],
        "condition": g["condition"],
        "approved_at": g["approved_at"],
        "guide": g["edited_json"],
        "checkoffs": g["checkoffs"],
    }


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
