"""Lab PDF extraction: one upload, zero typing.

The PDF goes to Claude as a native document block. Output is the pre-filled
confirmation screen's data: patient first name, age, lab values, medications
if present, and a condition guess. The doctor corrects, never types from scratch.
"""
import base64
import json
import anthropic
from .config import get_settings

EXTRACTION_SYSTEM = """You extract structured data from lab result PDFs (Quest, LabCorp, hospital labs, and similar).

Rules:
- Extract ONLY what is actually in the document. Never infer or invent values.
- Patient name: return the FIRST NAME and the LAST INITIAL only (a single capital letter, no period) — minimum-necessary PHI. Never return the full last name.
- Age: if only DOB is present, compute age from the collection date on the report (or today if absent).
- Values: every lab analyte you find, with value, unit, and the lab's own reference range if printed.
- prior_values: if the report shows previous results for the same analyte, capture them.
- Medications: only if explicitly listed on the document.
- condition_guess: your best single guess at the primary condition this panel addresses (e.g. "cholesterol", "diabetes"), lowercase. Empty string if unclear.
- confidence: "high" if this is a standard lab format you parsed cleanly, "low" if the document was unusual, partially legible, or ambiguous — low confidence tells the UI to ask the doctor to double-check.

Respond with ONLY valid JSON, no markdown fences:
{
  "first_name": "",
  "last_initial": "",
  "age": null,
  "values": {"LDL": "190 mg/dL"},
  "prior_values": {},
  "medications": [],
  "condition_guess": "",
  "confidence": "high"
}"""


def extract_from_pdf(pdf_bytes: bytes) -> dict:
    s = get_settings()
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    msg = client.messages.create(
        model=s.claude_model,
        max_tokens=2000,
        system=EXTRACTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": "Extract the structured data from this lab report."},
                ],
            }
        ],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
