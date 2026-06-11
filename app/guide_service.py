"""Guide generation: FastAPI -> Claude -> structured JSON -> Supabase.

Captures the training triple on every run:
  input  = input_data + clinical_rails       (stored on the guide row)
  output = generated_json                    (raw AI output, never mutated)
  truth  = edited_json                       (doctor's edits, the ground truth)
"""
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

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    try:
        msg = client.messages.create(
            model=s.claude_model,
            max_tokens=8000,
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


def approve_guide(guide_id: str) -> str:
    """Doctor clicked approve: timestamp it, mint the secure token."""
    db = get_db()
    token = secrets.token_urlsafe(24)
    db.table("guides").update(
        {
            "status": "approved",
            "secure_token": token,
            "approved_at": "now()",
        }
    ).eq("id", guide_id).execute()
    return token
