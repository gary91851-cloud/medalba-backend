"""Deterministic severity classification for closed-loop tracking.

Two tiers: 'normal' (all extracted values in range) vs 'abnormal' (any value
out of range, or any value we can't parse — fail toward caution).

This is RULE-BASED on purpose. The literature on LLM-as-CDSS shows model-based
abnormal flagging tops out well below the reliability a safety gate needs, so
the severity that drives the follow-up soft-stop is computed here from the raw
value + reference range, NOT by the language model. The model still writes the
plain-language guide; it does not decide the gate.

Verified against the live value format, e.g.:
    "Hemoglobin A1c": "8.4% (ref: 4.0-5.6%)"   -> abnormal
    "Creatinine":     "1.1 mg/dL (ref: 0.7-1.3 mg/dL)" -> normal
Handles one-sided (<x, >x, ≤x, ≥x) and two-sided (a-b) ranges. The dash in a
two-sided range is treated as a separator, never as a negative sign.
"""
import re

# Unsigned: the '-' in "7-56" is a separator between bounds, not a minus sign.
_NUM = re.compile(r"\d+(?:\.\d+)?")
_REF = re.compile(r"\(\s*ref[:\s]*(.*?)\)", re.IGNORECASE)


def _nums(s: str) -> list[float]:
    return [float(x) for x in _NUM.findall(s)]


def value_is_abnormal(raw) -> bool | None:
    """True = out of range, False = in range, None = unparseable.

    Boundary values (equal to a one-sided bound) are treated as in-range,
    matching common lab convention (a value == limit is not flagged)."""
    if not isinstance(raw, str):
        return None
    m = _REF.search(raw)
    if not m:
        return None  # no reference range present -> can't judge
    value_nums = _nums(raw[: m.start()])
    if not value_nums:
        return None
    value = value_nums[0]
    ref = m.group(1).strip()

    if "<" in ref or "\u2264" in ref:           # upper bound only
        b = _nums(ref)
        return None if not b else value > b[0]
    if ">" in ref or "\u2265" in ref:           # lower bound only
        b = _nums(ref)
        return None if not b else value < b[0]
    rn = _nums(ref)                              # two-sided a-b
    if len(rn) >= 2:
        lo, hi = rn[0], rn[1]
        return value < lo or value > hi
    return None


def classify_severity(values: dict | None) -> str:
    """Two-tier guide severity from the raw values dict.

    'abnormal' if any value is out of range OR unparseable (fail toward
    caution so the follow-up soft-stop errs on the side of prompting the
    doctor). 'normal' only when every value parses cleanly and is in range.
    Empty/missing values -> 'normal' (nothing to judge; avoids nagging on
    value-less guides). This empty-case default is the one tunable choice."""
    if not values:
        return "normal"
    saw_unparseable = False
    for raw in values.values():
        result = value_is_abnormal(raw)
        if result is True:
            return "abnormal"          # one clear abnormal is enough
        if result is None:
            saw_unparseable = True     # remember, but keep scanning for a clear hit
    return "abnormal" if saw_unparseable else "normal"
