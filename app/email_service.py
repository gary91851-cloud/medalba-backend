"""Transactional email via Resend.

Best-effort by design: a failed email never blocks signup or approval.
Without RESEND_API_KEY set, sends are skipped silently (local dev).
"""
import httpx
from .config import get_settings

BRAND_WRAP = """\
<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f6f3ea;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f3ea;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
  style="background:#ffffff;border:1px solid #e6e0d4;border-radius:14px;padding:36px 40px;
  font-family:Georgia,'Times New Roman',serif;color:#1d3a40;">
<tr><td style="text-align:center;padding-bottom:6px;">
  <span style="font-size:22px;font-weight:700;">Med<span style="color:#2e6e6a;">Alba</span></span><br>
  <span style="font-style:italic;color:#49616a;font-size:13px;">With you.</span>
</td></tr>
{body}
<tr><td style="padding-top:28px;border-top:1px solid #e6e0d4;text-align:center;
  font-family:Arial,sans-serif;font-size:11px;color:#49616a;line-height:1.6;">
  {footer}
</td></tr>
</table>
</td></tr></table></body></html>"""


def _send(to: str, subject: str, body_rows: str, footer: str) -> tuple[bool, str]:
    s = get_settings()
    if not s.resend_api_key:
        return False, "Email isn't configured yet — RESEND_API_KEY is missing on the server."
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {s.resend_api_key}"},
            json={
                "from": s.from_email,
                "to": [to],
                "subject": subject,
                "html": BRAND_WRAP.format(body=body_rows, footer=footer),
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True, ""
        text = r.text.lower()
        if r.status_code in (401,):
            return False, "Email service rejected the API key — re-check RESEND_API_KEY in Railway."
        if "verify a domain" in text or "testing emails" in text or r.status_code == 403:
            return False, ("Resend is in test mode: until you verify your domain, it only delivers to the "
                           "email address on your Resend account. Use that address to test, or verify your "
                           "domain at resend.com/domains.")
        return False, f"Email service error ({r.status_code}). Copy the link and send it yourself for now."
    except Exception:
        return False, "Couldn't reach the email service. Copy the link and send it yourself for now."


def send_welcome(to: str, provider_name: str, practice_name: str) -> tuple[bool, str]:
    body = f"""
<tr><td style="padding-top:22px;font-size:17px;line-height:1.7;">
  <p style="margin:0 0 14px;">Welcome, {provider_name} —</p>
  <p style="margin:0 0 14px;">{practice_name} is set up on MedAlba. Here's the whole workflow:</p>
  <p style="margin:0 0 6px;"><b>1.</b> Upload a lab PDF — we pull the values, no typing.</p>
  <p style="margin:0 0 6px;"><b>2.</b> Glance at what we found, click "Looks good."</p>
  <p style="margin:0 0 14px;"><b>3.</b> Review the drafted Guide and approve it. Your patient gets a personal,
  plain-English Guide — and you made every clinical decision in it.</p>
  <p style="margin:0 0 20px;">Your first 5 Guides are free. About 15 seconds each.</p>
</td></tr>
<tr><td align="center" style="padding:6px 0;">
  <a href="https://medalba-frontend.vercel.app" target="_blank"
    style="background-color:#2e6e6a;border-radius:9px;color:#ffffff;display:inline-block;
    font-family:Arial,sans-serif;font-size:17px;font-weight:bold;line-height:52px;
    text-align:center;text-decoration:none;width:280px;-webkit-text-size-adjust:none;">
    Create your first Guide</a>
</td></tr>"""
    footer = "You're receiving this because you created a MedAlba practice account."
    return _send(to, f"Welcome to MedAlba, {provider_name}", body, footer)


def send_guide(to: str, patient_first_name: str, practice_name: str, condition: str, link: str) -> tuple[bool, str]:
    body = f"""
<tr><td style="padding-top:22px;font-size:17px;line-height:1.7;">
  <p style="margin:0 0 14px;">Hi {patient_first_name},</p>
  <p style="margin:0 0 14px;">Your care team at <b>{practice_name}</b> prepared a personal Guide for you.
  It explains your recent results in plain English — what they mean, what's happening, and exactly
  what to do next, one day at a time.</p>
  <p style="margin:0 0 20px;">Your doctor reviewed and approved every word of it.</p>
</td></tr>
<tr><td align="center" style="padding:8px 0 4px;">
  <p style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:15px;color:#49616a;">Tap below to open your Guide:</p>
  <a href="{link}" target="_blank"
    style="font-family:Arial,sans-serif;font-size:18px;font-weight:bold;color:#2e6e6a;
    text-decoration:underline;word-break:break-all;line-height:1.6;">{link}</a>
</td></tr>
<tr><td align="center" style="padding:14px 0 4px;">
  <a href="{link}" target="_blank"
    style="background-color:#2e6e6a;border-radius:9px;color:#ffffff;display:inline-block;
    font-family:Arial,sans-serif;font-size:16px;font-weight:bold;line-height:48px;
    text-align:center;text-decoration:none;width:260px;-webkit-text-size-adjust:none;">
    Open your Guide</a>
</td></tr>
<tr><td style="text-align:center;font-family:Arial,sans-serif;font-size:12px;color:#49616a;padding-bottom:14px;">
  No account or password needed — the link is private to you.
</td></tr>
"""
    footer = (f"This Guide was sent by {practice_name}. It is educational support approved by your doctor "
              "and is not a substitute for medical advice. For clinical questions, contact your doctor's office.")
    return _send(to, f"{patient_first_name}, your personal Guide from {practice_name}", body, footer)
