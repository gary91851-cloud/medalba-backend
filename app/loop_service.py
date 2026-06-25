"""Closed-loop tracking: open a loop when a guide is created, advance it
through reviewed/sent/acknowledged, and close it manually — appending an
immutable audit event at every step.

Safe for pre-migration guides: advance_loop no-ops if no loop row exists,
so approving/sending older guides never crashes.
"""
from .db import get_db

# Forward-only ordering. A loop never moves backward, and a repeated
# transition (e.g. a patient opening their Guide twice) is a no-op — which
# keeps the audit trail clean and the first-open timestamp stable.
_RANK = {"resulted": 0, "reviewed": 1, "sent": 2, "acknowledged": 3, "action_due": 4, "closed": 5}


def open_loop(guide_id: str, patient_id: str, practice_id: str,
              provider_id: str, result_label: str, severity: str = "abnormal") -> None:
    """Called at guide creation. Opens a loop at 'resulted' + first audit event.
    Best-effort: never blocks guide creation if loop bookkeeping fails."""
    db = get_db()
    try:
        loop = db.table("loops").insert({
            "practice_id": practice_id,
            "provider_id": provider_id,
            "patient_id": patient_id,
            "guide_id": guide_id,
            "result_label": result_label or "Lab result",
            "severity": severity,
            "status": "resulted",
        }).execute().data[0]
        db.table("loop_events").insert({
            "loop_id": loop["id"],
            "event_type": "loop_opened",
            "actor": "system",
            "metadata": {"from_status": None, "to_status": "resulted"},
        }).execute()
    except Exception:
        pass  # never block guide creation on loop bookkeeping


def advance_loop(guide_id: str, to_status: str, *, actor: str = "provider",
                 actor_id: str | None = None, action_type: str | None = None,
                 action_due_at: str | None = None, action_note: str | None = None,
                 metadata: dict | None = None) -> None:
    """Advance the loop for a guide to a new status (forward-only), writing
    milestone timestamps and an append-only audit event. No-op if the guide
    predates the loop system, or if the loop is already at/past this status."""
    db = get_db()
    try:
        rows = db.table("loops").select("id, status").eq("guide_id", guide_id).execute().data
        if not rows:
            return  # pre-migration guide, nothing to advance
        loop = rows[0]

        # forward-only guard: never move backward, never re-fire the same stage
        if _RANK.get(to_status, 99) <= _RANK.get(loop["status"], -1):
            return

        updates: dict = {"status": to_status}
        if to_status == "reviewed":
            updates["reviewed_at"] = "now()"
        elif to_status == "sent":
            updates["sent_at"] = "now()"
        elif to_status == "acknowledged":
            updates["acknowledged_at"] = "now()"

        if action_type is not None:
            updates["action_type"] = action_type
        if action_due_at is not None:
            updates["action_due_at"] = action_due_at
        if action_note is not None:
            updates["action_note"] = action_note

        db.table("loops").update(updates).eq("id", loop["id"]).execute()
        db.table("loop_events").insert({
            "loop_id": loop["id"],
            "event_type": to_status,
            "actor": actor,
            "actor_id": actor_id,
            "metadata": {"from_status": loop["status"], "to_status": to_status, **(metadata or {})},
        }).execute()
    except Exception:
        pass  # best-effort; never block the underlying guide action


def acknowledge_loop_by_guide(guide_id: str, channel: str = "secure_link") -> None:
    """The patient opened their Guide. Advance the loop to 'acknowledged'
    (first open only, via the forward-only guard) and log it as a patient event."""
    advance_loop(guide_id, "acknowledged", actor="patient",
                 metadata={"opened_via": channel})


def close_loop(loop_id: str, provider: dict) -> str:
    """Manually close a loop. Practice-scoped. Records who/when and method.
    Returns: 'ok' | 'already' | 'forbidden' | 'missing'."""
    db = get_db()
    rows = db.table("loops").select("id, status, practice_id").eq("id", loop_id).execute().data
    if not rows:
        return "missing"
    loop = rows[0]
    if loop["practice_id"] != provider["practice_id"]:
        return "forbidden"
    if loop["status"] == "closed":
        return "already"
    db.table("loops").update({
        "status": "closed",
        "closed_at": "now()",
        "closed_method": "manual",
        "closed_by_user": provider["id"],
        "action_completed_at": "now()",
    }).eq("id", loop_id).execute()
    db.table("loop_events").insert({
        "loop_id": loop_id,
        "event_type": "closed",
        "actor": "provider",
        "actor_id": provider["id"],
        "metadata": {"from_status": loop["status"], "to_status": "closed", "method": "manual"},
    }).execute()
    return "ok"


def get_loop_for_guide(guide_id: str) -> dict | None:
    """Return the loop + its chronological events for a guide, for the timeline view."""
    db = get_db()
    rows = db.table("loops").select("*").eq("guide_id", guide_id).execute().data
    if not rows:
        return None
    loop = rows[0]
    events = (
        db.table("loop_events")
        .select("*")
        .eq("loop_id", loop["id"])
        .order("created_at", desc=False)
        .execute()
        .data
    )
    return {"loop": loop, "events": events}
