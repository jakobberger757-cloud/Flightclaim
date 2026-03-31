Context
_send_result_email currently sends a single generic template regardless of how the user got there.
We want three distinct email experiences:

claim_form_submitted → "We've received your claim" confirmation
remind_later_high_confidence → "Your estimate is saved, reply when ready"
email_me_result (default) → "Here's your refund estimate"

Additionally, claim_form_submitted and remind_later_high_confidence captures go through /capture-email, not /email-result, so they never trigger any transactional email today. This change adds that send.
Confirmed: source string in HTML
remind_later_high_confidence confirmed at flightclaim-demo.html line 1004 in doCapture().
Files to modify

proxy.py only

Current state (key lines)

EmailResultRequest model: lines 148–151
_send_result_email: lines 360–405 (single template, no source param)
/capture-email signature: line 250 — async def capture_email(data: EmailCaptureRequest):
/capture-email operator-alert block starts: line 272 — if data.source == "claim_form_submitted":
background_tasks.add_task(...) in /email-result: line 335
BackgroundTasks already imported (line 19)

Changes (5 surgical edits)
Change 1 — EmailResultRequest: add source field
After session_id: Optional[str] = None (line 151), add:
python    source: Optional[str] = "email_me_result"
Change 2 — Replace entire _send_result_email function
Replace lines 360–405 with the new multi-branch version that accepts source: str = "email_me_result" and renders three distinct HTML templates. Uses FROM_EMAIL env var consistently (fixes the onboarding@resend.dev fallback bug). Adds reply_to.
Change 3 — Pass source to background task in /email-result
Line 335: append , request.source or "email_me_result" to the add_task call.
Change 4 — Add transactional send block in /capture-email
Insert immediately BEFORE if data.source == "claim_form_submitted": (line 272):
python    if data.source in ("claim_form_submitted", "remind_later_high_confidence") and data.estimated_refund:
        try:
            _refund = data.estimated_refund
            _you_keep = round(_refund * 0.80)
            _result_dict = {
                "airline": data.airline or "your airline",
                "reason": f"Based on your {data.airline or 'airline'} cancellation or delay, you appear eligible for a cash refund under DOT regulations.",
            }
            background_tasks.add_task(_send_result_email, data.email, _result_dict, _refund, _you_keep, data.source)
        except Exception as e:
            print(f"Transactional email error: {e}")
This requires background_tasks in the function signature (Change 5).
Change 5 — Add BackgroundTasks to /capture-email signature
Line 250: async def capture_email(data: EmailCaptureRequest): →
async def capture_email(data: EmailCaptureRequest, background_tasks: BackgroundTasks):
No duplicate sends

email_me_result path: user goes through /email-result → _send_result_email called there. /capture-email does NOT trigger a send for this source (the new block is gated on claim_form_submitted and remind_later_high_confidence only).
claim_form_submitted and remind_later_high_confidence: only sent from /capture-email. Neither hits /email-result.

Verification

Deploy to Railway
Submit a claim form → user receives "We've received your claim" email
Click "Remind me later" → user receives "Your estimate is saved" email
Click "Email me this result" → user receives "Here's your refund estimate" email (via /email-result, unchanged path)
Check Railway logs for EMAIL TASK SUCCESS on all three paths
Confirm FROM_EMAIL is set in Railway so no onboarding@resend.dev fallback appears
