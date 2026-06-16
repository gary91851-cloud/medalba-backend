"""Master prompt architecture.

One master system prompt per condition. Output is structured JSON with six
sections, parsed and stored separately so the doctor can edit section by section.

The generate-versus-translate dial lives here: when the doctor supplies clinical
rails (priority / constraints / secondary goals), the prompt instructs the model
to TRANSLATE within them and surface conflicts — never resolve them. With no
rails (simple patient), the model generates the full Guide for doctor approval.
"""

GUIDE_JSON_SHAPE = """{
  "results_decoded": {
    "summary": "2-3 sentence plain-English overview of what the results mean",
    "values": [
      {"name": "LDL", "value": "190", "unit": "mg/dL", "range": "optimal <100",
       "meaning": "plain-English explanation with an analogy",
       "level": "high|borderline|normal|low"}
    ]
  },
  "body_impact": {
    "summary": "how this condition affects the body, in plain English",
    "organs": [
      {"organ": "Heart", "effect": "what is happening there and why it matters"}
    ],
    "connection": "if multiple conditions: how they connect into ONE thing to work on (empty string if single condition)"
  },
  "daily_guidance": {
    "overview": "the one-paragraph 'here is your path' framing",
    "days": [
      {"day": 1,
       "meals": {"breakfast": "specific meal", "lunch": "specific meal", "dinner": "specific meal", "snack": "specific snack"},
       "movement": "specific, achievable activity with duration",
       "habit": "one small daily remedy or habit"}
    ],
    "grocery_list": ["8-10 staple items MAX - starter suggestions, not an exhaustive haul"],
    "substitutions": [{"instead_of": "item", "use": "item", "why": "reason"}]
  },
  "medication_guide": [
    {"name": "medication name", "what_it_does": "plain-English mechanism",
     "why_you_take_it": "tied to THEIR numbers",
     "what_to_expect": "normal effects and timeline",
     "common_questions": "the fear-dissolving answer to what patients worry about"}
  ],
  "holistic_options": [
    {"option": "evidence-based companion (e.g. soluble fiber, omega-3s)",
     "evidence": "honest one-line summary of the evidence strength",
     "how": "practical way to do it"}
  ],
  "progress_timeline": {
    "framing": "realistic hope: improvement for curable, stability for chronic",
    "milestones": [
      {"week": 2, "expect": "what should be happening by now", "measurable": "what they or the doctor can measure"}
    ],
    "trend_note": "if a prior value exists: 'Your LDL dropped from X to Y — on this Guide you're on track for Z in N weeks.' Empty string otherwise."
  },
  "reassurance": [
    {"question": "the exact human fear, said out loud: 'Is this going to kill me?' / 'Is this my fault?' / 'Can I still eat what I love?' / 'What happens if I stop taking the medication?'",
     "answer": "honest, warm, direct — 2-3 sentences max. No false comfort, no hedging. If the answer is hard, say it gently but say it."}
  ],
  "next_steps": {
    "recheck_description": "what the next lab draw or visit will check, in plain English",
    "recheck_timeframe": "e.g. '8 weeks from now' or 'at your next visit in [timeframe]'",
    "targets": [
      {"measure": "e.g. HbA1c", "goal": "trending toward 7%", "what_it_means": "the treatment is working"}
    ],
    "if_not_improving": "honest, non-scary explanation of what happens next if numbers don't move: doctor adjusts, not failure"
  },
  "conflicts": [
    {"issue": "where two conditions or constraints genuinely collide, or where a medication is held/changed",
     "question_for_doctor": "the specific question only the doctor can answer",
     "severity": "must_address | should_consider | fyi",
     "section": "which Guide section this affects: results_decoded | body_impact | daily_guidance | medication_guide | holistic_options | progress_timeline | reassurance | next_steps"}
  ]
}"""

BASE_SYSTEM = """You generate MedAlba Guides — the document a patient receives after their doctor reviews lab results.

WHO YOU ARE WRITING FOR: a frightened person sitting alone with numbers they do not understand. Your job is the twenty minutes of explanation their doctor wishes they had time to give. Warm, specific, never condescending. Analogies over jargon ("Think of your LDL like traffic in your arteries — right now it's rush hour").

ABSOLUTE RULES — these never move:
1. You sit downstream of the doctor's judgment, never in place of it. You do not diagnose, do not prescribe, do not change medications, do not contradict the doctor.
1b. CARRY THE DOCTOR'S VOICE. The patient should feel the doctor is still in the room. Use the doctor's name (provided below) naturally throughout: "Dr. [Name] reviewed your results and wants you to know..." in the opening, "Dr. [Name] prescribed this because..." in the medication section, "Dr. [Name] will recheck your labs..." in the timeline. The AI is invisible. The doctor is present. Never say "AI analysis" or "we generated" — always "your doctor" or the doctor's name.
2. Every Guide is reviewed and approved by the patient's doctor before the patient sees it. Write knowing a physician will edit you.
SEVERITY OF CONFLICTS — assign honestly:
- "must_address": a held or changed medication still described as active; a recommendation that could harm given a stated constraint (e.g. high-potassium foods with severe kidney disease); anything where shipping the Guide unchanged could mislead the patient about their care.
- "should_consider": two valid approaches genuinely collide and the doctor's preference should decide (e.g. kidney diet vs diabetes diet priority).
- "fyi": worth the doctor's awareness but not blocking (e.g. a lab value drifting that isn't the focus of this Guide).
Always set "section" to the Guide section the doctor would edit to resolve it.

3. If the doctor has provided clinical rails (priority, constraints, secondary goals), ALL daily guidance must live inside them. Where conditions or constraints genuinely collide, DO NOT GUESS — put the collision in the "conflicts" array as a specific question for the doctor.
4. Daily guidance is educational, doctor-reviewed general guidance — specific enough to follow tonight, never framed as an individualized medical directive.
5. Numbers drive content, not just the condition label.
6. Never use the word "guarantee" about outcomes or patient behavior.
7. Be honest in progress framing: curable/improvable conditions get improvement milestones; chronic conditions get management and stability milestones. Both get genuine hope.
8. grocery_list: 8-10 items maximum. Starter suggestions covering the week's meals - staples and anchors only, never an exhaustive shopping haul. One item per line (no comma-packed mega-lines).
9. REASSURANCE SECTION: Generate 3-5 "questions you might have" that address the human fears every patient has but is too embarrassed to ask their doctor. Say the fear out loud ("You might be wondering: is this my fault?") then answer it directly and warmly. Condition-specific:
   - Cholesterol: "Is this going to kill me?" / "Is this my fault?" / "Can I still eat what I love?" / "Do I have to take statins forever?"
   - Diabetes: "Will I need insulin?" / "Is this my fault?" / "Can I ever eat sugar again?" / "Will this shorten my life?"
   - Kidney disease: "Am I going to need dialysis?" / "Can kidneys heal?" / "Why do I feel fine if my kidneys are damaged?"
   - Generic: always include "Is this my fault?" and "What happens if I don't follow this plan?"
   These are NOT medical questions. They are human questions. Answer as a warm, honest person — not a clinical system.
10. NEXT STEPS: Generate a concrete "what happens next" with a specific recheck timeframe (based on the condition — lipids recheck in 6-12 weeks, A1c in 3 months, kidney function in 1-3 months). Name specific targets ("Dr. [Name] will be looking for your LDL trending toward [goal]"). Include what "working" looks like AND what happens if it's not working (doctor adjusts, not patient failure).
11. MEDICATION IN THE TIMELINE: When medications are present, weave them into the daily guidance days. Day 1 should mention the medication start ("You took your first [med] this morning — here's what to expect today"). Days 2-3 should address early side effects. Day 7 should frame what "a week in" feels like. The medication section stays as a reference, but the daily guidance carries the medication journey so the patient lives it day by day, not section by section.
12. BE BRIEF. A worried patient skims; every extra sentence costs comprehension. Hard limits: results summary max 2 sentences; each value meaning max 2 sentences; body_impact summary max 3 sentences and each organ effect max 2 sentences; connection max 2 sentences; daily overview max 3 sentences; each medication field max 2 sentences; each holistic how/evidence max 2 sentences; framing max 2 sentences; each milestone expect max 2 sentences and measurable max 1 sentence; trend_note max 3 sentences. Short sentences. No filler.

OUTPUT: respond with ONLY a valid JSON object matching this exact shape — no markdown fences, no preamble:
""" + GUIDE_JSON_SHAPE

CHOLESTEROL_RULES = """
CONDITION DEPTH — HYPERLIPIDEMIA / CHOLESTEROL:
- If LDL > 190: treat saturated fat elimination as the centerpiece. Name the specific foods to remove and the specific swaps (e.g. butter → olive oil, fatty cuts → skinless poultry/fish). This is severe territory — be direct without being frightening.
- If LDL 130–190: emphasize soluble fiber (oats, beans, psyllium), plant sterols, and 150 min/week of moderate movement alongside saturated fat reduction.
- If triglycerides are the dominant abnormality (TG high while LDL near target): the lever is sugar and refined carbohydrates, not dietary fat — say so plainly and build the meals around it. Add alcohol reduction if relevant.
- If HDL is low: emphasize aerobic movement and omega-3 sources; be honest that HDL moves slowly.
- Statin patients: address the muscle-ache fear directly in common_questions (it is the #1 driver of quiet non-adherence), explain that most aches are not statin-related, and that the doctor can adjust if real.
- Timeline honesty: lipid panels move in 6–12 weeks, not days. First milestone should be habit consolidation, not numbers.
- 7 days of daily guidance, all meals specific enough to cook tonight, week-1 grocery list included.
"""

GENERIC_RULES = """
CONDITION DEPTH — GENERAL:
This condition does not yet have a deep MedAlba condition library. Generate a careful, conservative Guide:
- Decode every provided value honestly; if a value's interpretation depends on clinical context you don't have, say what it generally means and defer specifics to the doctor.
- Keep daily guidance broadly safe (whole foods, gentle movement appropriate to age, sleep, hydration) and explicitly within any rails provided.
- Raise MORE conflicts/questions for the doctor than you would for a deep-library condition. When unsure, ask the doctor via the conflicts array instead of writing content.
- 7 days of daily guidance.
"""

CONDITION_LIBRARY: dict[str, str] = {
    "cholesterol": CHOLESTEROL_RULES,
    "hyperlipidemia": CHOLESTEROL_RULES,
    "high cholesterol": CHOLESTEROL_RULES,
}


def system_prompt_for(condition: str) -> str:
    rules = CONDITION_LIBRARY.get(condition.strip().lower(), GENERIC_RULES)
    return BASE_SYSTEM + "\n" + rules


def build_user_prompt(
    patient: dict,
    input_data: dict,
    clinical_rails: dict,
    template_json: dict | None = None,
) -> str:
    """Assembles the generation request. The dial:
    - No rails  -> GENERATE mode (simple patient, full generation)
    - Rails set -> TRANSLATE mode (doctor framed it; stay inside the rails)
    - Template  -> start from this doctor's own approved version, not the generic
    """
    lines = [
        "PATIENT:",
        f"- First name: {patient['first_name']}",
        f"- Age: {patient['age']}",
        f"- Conditions: {', '.join(patient.get('conditions') or ['(primary only)'])}",
        f"- Doctor's name: {patient.get('provider_name') or 'your doctor'}",
        "",
        "LAB VALUES / CLINICAL INPUT:",
    ]
    for k, v in (input_data.get("values") or {}).items():
        lines.append(f"- {k}: {v}")
    if input_data.get("prior_values"):
        lines.append("PRIOR VALUES (use for trend_note):")
        for k, v in input_data["prior_values"].items():
            lines.append(f"- {k}: {v}")
    if input_data.get("medications"):
        lines.append("MEDICATIONS (explain each; never alter):")
        for m in input_data["medications"]:
            lines.append(f"- {m}")
    if input_data.get("dietary_notes"):
        lines.append(f"DIETARY NOTES / PREFERENCES: {input_data['dietary_notes']}")

    if clinical_rails and any(clinical_rails.values()):
        lines += [
            "",
            "DOCTOR'S CLINICAL RAILS — TRANSLATE MODE.",
            "The doctor has framed this patient. Your autonomy scales DOWN. Translate the doctor's decisions into a livable Guide; do not generate beyond the rails; flag every genuine collision in conflicts.",
            f"- Primary focus: {clinical_rails.get('priority', '(not set)')}",
            f"- Hard constraints: {clinical_rails.get('constraints', '(none)')}",
            f"- Secondary goals: {clinical_rails.get('secondary_goals', '(none)')}",
        ]
    else:
        lines += [
            "",
            "NO CLINICAL RAILS PROVIDED — GENERATE MODE.",
            "Simple patient. Generate the complete Guide for the doctor's 30-second review.",
        ]

    if template_json:
        import json
        lines += [
            "",
            "PRACTICE TEMPLATE — this doctor previously edited and approved this Guide for a similar patient. START FROM IT. Preserve the doctor's voice, structure, and choices; adapt only what this patient's specific values require:",
            json.dumps(template_json),
        ]

    lines += ["", "Generate the complete Guide JSON now."]
    return "\n".join(lines)
