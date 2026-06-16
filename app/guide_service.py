"""Guide generation: FastAPI -> Claude -> structured JSON -> Supabase.

Captures the training triple on every run:
  input  = input_data + clinical_rails       (stored on the guide row)
  output = generated_json                    (raw AI output, never mutated)
  truth  = edited_json                       (doctor's edits, the ground truth)
"""
import json
import json
import secrets
import anthropic
from .config import get_settings
from .db import get_db
from .prompts import system_prompt_for, build_user_prompt


def _best_template(db, practice_id: str, condition: str, age: int) -> dict | None:
    """Practice-level saved templates override the generic master prompt."""
    res = (
        db.table("templates")
        .select("guide_json, age_min, age_max")
        .eq("practice_id", practice_id)
        .eq("condition", condition.strip().lower())
        .order("created_at", desc=True)
        .execute()
    )
    for t in res.data or []:
        lo = t.get("age_min") or 0
        hi = t.get("age_max") or 120
        if lo <= age <= hi:
            return t["guide_json"]
    return None


def _parse_guide_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_guide(guide_id: str) -> None:
    """Runs the generation for a guide row already created with status='generating'."""
    db = get_db()
    s = get_settings()

    guide = db.table("guides").select("*").eq("id", guide_id).single().execute().data
    patient = (
        db.table("patients").select("*").eq("id", guide["patient_id"]).single().execute().data
    )

    template = _best_template(db, guide["practice_id"], guide["condition"], patient["age"])

    # Fetch provider name so the Guide carries the doctor's voice
    provider = db.table("providers").select("full_name").eq("id", guide["provider_id"]).single().execute().data
    patient["provider_name"] = (provider or {}).get("full_name") or "your doctor"

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    try:
        msg = client.messages.create(
            model=s.claude_model,
            max_tokens=16000,
            system=system_prompt_for(guide["condition"]),
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(
                        patient, guide["input_data"], guide["clinical_rails"], template
                    ),
                }
            ],
        )
        parsed = _parse_guide_json(msg.content[0].text)
        conflicts = parsed.pop("conflicts", [])
        db.table("guides").update(
            {
                "generated_json": parsed,
                "edited_json": parsed,  # doctor's working copy starts as the AI output
                "conflicts": conflicts,
                "status": "pending_review",
                "model_used": s.claude_model,
            }
        ).eq("id", guide_id).execute()
    except Exception as e:
        db.table("guides").update(
            {"status": "failed", "generated_json": {"error": str(e)}}
        ).eq("id", guide_id).execute()
        raise


def regenerate_section(guide_id: str, section: str, instruction: str) -> None:
    """Doctor rejected the AI's approach for one section. Regenerate ONLY that
    section under the doctor's explicit instruction (translate mode, max autonomy down)."""
    import json
    db = get_db()
    s = get_settings()
    guide = db.table("guides").select("*").eq("id", guide_id).single().execute().data
    patient = db.table("patients").select("*").eq("id", guide["patient_id"]).single().execute().data

    current = (guide.get("edited_json") or {}).get(section)
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    sys = system_prompt_for(guide["condition"]) + (
        "\n\nYou are REVISING ONE SECTION of an existing Guide under the doctor's explicit instruction. "
        "Output ONLY a valid JSON object for that single section, matching the same shape it currently has. "
        "No markdown, no preamble, no other sections."
    )
    user = (
        f"PATIENT: {patient['first_name']}, age {patient['age']}, conditions: "
        f"{', '.join(patient.get('conditions') or [])}.\n\n"
        f"SECTION TO REVISE: {section}\n\n"
        f"CURRENT VERSION:\n{json.dumps(current)}\n\n"
        f"DOCTOR'S INSTRUCTION (follow exactly): {instruction}\n\n"
        f"Return the revised '{section}' JSON only."
    )
    msg = client.messages.create(model=s.claude_model, max_tokens=6000, system=sys,
                                 messages=[{"role": "user", "content": user}])
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    revised = json.loads(text.strip())
    edited = guide.get("edited_json") or {}
    edited[section] = revised
    db.table("guides").update({"edited_json": edited}).eq("id", guide_id).execute()


def approve_guide(guide_id: str, provider_id: str) -> str:
    """Doctor clicked approve: timestamp it, mint the secure token, write immutable audit row."""
    import hashlib
    db = get_db()
    token = secrets.token_urlsafe(24)

    # Fetch the full guide + patient for the snapshot
    guide = db.table("guides").select("*").eq("id", guide_id).single().execute().data
    patient = db.table("patients").select("*").eq("id", guide["patient_id"]).single().execute().data

    # Write the immutable audit row BEFORE updating the guide status
    approved_json = guide.get("edited_json") or guide.get("generated_json") or {}
    db.table("guide_approvals").insert({
        "guide_id": guide_id,
        "provider_id": provider_id,
        "practice_id": guide["practice_id"],
        "approved_json_hash": hashlib.sha256(
            json.dumps(approved_json, sort_keys=True).encode()
        ).hexdigest(),
        "approved_json": approved_json,
        "generated_json": guide.get("generated_json") or {},
        "input_data": guide.get("input_data") or {},
        "clinical_rails": guide.get("clinical_rails") or {},
        "conflicts": guide.get("conflicts") or [],
        "acknowledged_flags": guide.get("acknowledged_flags") or [],
        "model_used": guide.get("model_used"),
        "patient_first_name": patient["first_name"],
        "patient_age": patient["age"],
        "condition": guide["condition"],
    }).execute()

    # Auto-learn: silently upsert a practice template from this approved Guide.
    # The doctor's edits become the starting point for future similar patients.
    # Uses the newest approved Guide per condition as the template (no manual save needed).
    try:
        condition = guide["condition"].strip().lower()
        existing = (
            db.table("templates")
            .select("id")
            .eq("practice_id", guide["practice_id"])
            .eq("provider_id", provider_id)
            .eq("condition", condition)
            .eq("name", f"_auto_{condition}")
            .execute()
            .data
        )
        template_data = {
            "practice_id": guide["practice_id"],
            "provider_id": provider_id,
            "condition": condition,
            "name": f"_auto_{condition}",
            "guide_json": approved_json,
            "age_min": max(0, patient["age"] - 15),
            "age_max": min(120, patient["age"] + 15),
        }
        if existing:
            db.table("templates").update(template_data).eq("id", existing[0]["id"]).execute()
        else:
            db.table("templates").insert(template_data).execute()
    except Exception:
        pass  # auto-learn is best-effort; never blocks approval

    # Now update the guide itself
    db.table("guides").update(
        {
            "status": "approved",
            "secure_token": token,
            "approved_at": "now()",
        }
    ).eq("id", guide_id).execute()
    return token
