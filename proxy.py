"""
FlightClaim - Production Proxy Server (Final Launch Version)
------------------------------------------------------------
5 launch improvements:
1. Two-layer rate limiting (5/min + 20/hour) — cost protection
2. Email capture for non-converters — retargeting list
3. "Email me this result" endpoint — delayed conversion
4. Recent wins endpoint — dynamic social proof
5. Better error messages — guides correct input
"""

import os
import json
import time
import anthropic
import resend
from datetime import datetime
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, EmailStr
from typing import Optional

app = FastAPI(docs_url=None, redoc_url=None)
resend.api_key = os.environ.get("RESEND_API_KEY")

ALLOWED_ORIGINS = [
    os.environ.get("FRONTEND_URL", ""),
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in ALLOWED_ORIGINS if o],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

# ── 1. TWO-LAYER RATE LIMITING ────────────────────────────────────────────────
# 5/minute stops burst abuse. 20/hour stops sustained draining.
# Real users on Reddit won't hit either limit.

request_counts: dict = defaultdict(list)


def check_rate_limit(ip: str):
    now = time.time()
    request_counts[ip] = [t for t in request_counts[ip] if t > now - 3600]
    minute_count = sum(1 for t in request_counts[ip] if t > now - 60)
    hour_count = len(request_counts[ip])

    if minute_count >= 5:
        raise HTTPException(429, "Max 5 checks per minute — try again in a moment.")
    if hour_count >= 20:
        raise HTTPException(429, "Hourly limit reached. Max 20 checks per hour.")

    request_counts[ip].append(now)


# ── 2 + 3. EMAIL CAPTURE STORE ───────────────────────────────────────────────
# In-memory for v1. Add Supabase later.
email_captures: list = []
recent_wins_store: list = []

SEEDED_WINS = [
    {"amount": 412, "airline": "United Airlines", "hours_ago": 3, "illustrative": True},
    {"amount": 287, "airline": "Delta Air Lines", "hours_ago": 7, "illustrative": True},
    {"amount": 521, "airline": "American Airlines", "hours_ago": 11, "illustrative": True},
]

# ── PROMPT ────────────────────────────────────────────────────────────────────

ANALYZE_PROMPT = """You are an expert in US airline passenger rights and DOT regulations.

Analyze this airline email and determine what the passenger is owed.

DOT KEY RULES (2024):
- Cancelled flight = full cash refund required, IF passenger did not accept rebooking or alternative compensation
- Domestic delay/significant schedule change 3+ hours = refund eligible IF passenger chooses not to travel
- International delay 6+ hours = refund eligible if passenger chooses not to travel
- Airlines must process credit card refunds within 7 business days

PASSENGER CONTEXT: accepted_rebooking = {accepted_rebooking}
If accepted_rebooking is true, the passenger accepted alternative transportation or compensation.
In that case, they are generally NOT eligible for a cash refund - set eligible=false unless there are exceptional circumstances.

IMPORTANT: If this is a booking confirmation (not a cancellation or delay), 
set eligible=false and wrong_email_type=true.

EMAIL TO ANALYZE:
{email_text}

Return ONLY JSON:
{{
  "email_type": "cancellation | delay | booking_confirmation | refund_response | other",
  "flight_number": "e.g. UA2047 or null",
  "airline": "full airline name or null",
  "departure_date": "YYYY-MM-DD or null",
  "origin": "3-letter airport code or null",
  "destination": "3-letter airport code or null",
  "booking_reference": "confirmation number or null",
  "ticket_price": number or null,
  "is_cancelled": true or false,
  "delay_minutes": number or null,
  "eligible": true or false,
  "estimated_refund_min": number or null,
  "estimated_refund_max": number or null,
  "confidence_score": number between 0 and 1,
  "confidence_reason": "brief explanation",
  "reason": "2-3 sentences plain English explanation",
  "dot_regulation": "specific regulation citation or null",
  "recommended_action": "auto_file | manual_file | not_eligible | needs_more_info",
  "our_fee_estimate": number or null,
  "user_keeps_estimate": number or null,
  "wrong_email_type": true or false
}}

Return ONLY valid JSON. No markdown."""


# ── MODELS ────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    email_text: str
    session_id: Optional[str] = None
    accepted_rebooking: Optional[bool] = False  # User answered the rebooking question


class EmailCaptureRequest(BaseModel):
    email: EmailStr
    estimated_refund: Optional[float] = None
    airline: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    confidence_score: Optional[float] = None
    eligible: Optional[bool] = None
    flight_number: Optional[str] = None
    accepted_rebooking: Optional[bool] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    result_state: Optional[str] = None
    original_email_text: Optional[str] = None
    subject_line: Optional[str] = None
    free_text_description: Optional[str] = None


class EmailResultRequest(BaseModel):
    email: EmailStr
    result: dict
    session_id: Optional[str] = None
    source: Optional[str] = "email_me_result"


class RadarAnalyzeRequest(BaseModel):
    post: str
    operator_key: Optional[str] = None


RADAR_PROMPT = """You are an expert Reddit opportunity analyst for FlightClaim, a service that helps travelers get cash refunds from cancelled/delayed flights under DOT regulations.

Your job: evaluate a Reddit post and draft reply strategies.

## SCORING (0-10)
Score higher when:
- Posted recently (today/this week) — stale posts waste effort
- OP mentions being offered a voucher instead of cash refund
- OP is confused about their rights or what they're owed
- OP explicitly asks "what should I do" or "what am I entitled to"
- US airline involved (DOT rules only apply to US carriers / flights to/from US)
- Cancellation (stronger DOT protection) vs delay (weaker)
- OP mentions refund specifically

Score lower (deprioritize) when:
- Pure venting with no question — OP isn't looking for help
- Already resolved — OP got their money back
- Non-US airline or non-US domestic flight
- Post is old (weeks/months)
- OP accepted a rebooking willingly and seems fine with it

## REPLY TONE RULES
- Sound like a real traveler, not a company
- Be frustrated with airlines on their behalf — empathize first
- Lead with VALUE — educate about their rights before anything else
- Never corporate-speak, never founder-pitch, never say "our service" or "I built"
- Never lie about who you are in reply_1 or reply_2
- Short-to-medium length. Conversational. No bullet lists unless natural.

## THREE REPLY STRATEGIES

**reply_1 — Value only**
Pure education. Tell them their rights under DOT. What they're owed. How to ask for it. No mention of any service, no link, no hint you have a product. Just be helpful. This is the safest reply for any subreddit.

**reply_2 — Soft personal mention**
Lead with value (2-3 sentences of real help), then naturally mention you've helped people with this before — something like "I've been helping a few people navigate this lately, happy to walk you through it if you want to reply here." No link. No service name. Just a personal offer to help further.

**reply_3 — Link if earned**
Only include a link to flightclaim.today if:
- The post is high-intent (OP is actively asking for help, not just venting)
- The subreddit allows helpful links (not r/travel or airline subs which ban promotion)
- Including a link genuinely helps them more than reply_1 or reply_2 would alone
If the link is NOT earned, set link_included: false and explain why in "reason", then write the reply as a better version of reply_2 instead.
If the link IS earned, weave it in naturally: "I actually built a tool for exactly this — [flightclaim.today](https://flightclaim.today) — it analyzes your cancellation email and tells you what you're owed in about 30 seconds." Keep it one mention, not a pitch.

## OUTPUT FORMAT
Return ONLY valid JSON. No markdown, no commentary, no explanation outside the JSON.

{{
  "score": <0-10 integer>,
  "recommended_action": <"Reply now" | "Reply with caution" | "Skip">,
  "why": "<1-2 sentence explanation of score and action>",
  "mod_risk": <"Low" | "Medium" | "High">,
  "mod_risk_reason": "<brief reason>",
  "conversion_likelihood": <"Low" | "Medium" | "High">,
  "conversion_reason": "<brief reason>",
  "best_next_move": "<one clear sentence: what to post and where>",
  "stress_test": {{
    "spammy_risk": <"Low" | "Medium" | "High">,
    "link_hurts_trust": <true | false>,
    "better_as_value_only": <true | false>,
    "op_likely_to_respond": <true | false>,
    "enough_context": <true | false>,
    "link_earned_or_unearned": <"Earned" | "Unearned">,
    "non_us_risk": <"Low" | "Medium" | "High">
  }},
  "reply_1": {{
    "style": "Value only",
    "text": "<the reply text>"
  }},
  "reply_2": {{
    "style": "Soft mention",
    "text": "<the reply text>"
  }},
  "reply_3": {{
    "style": "Link if earned",
    "text": "<the reply text>",
    "link_included": <true | false>,
    "reason": "<why link was included or excluded>"
  }},
  "checklist": {{
    "sounds_human": <true | false>,
    "appropriate_length": <true | false>,
    "value_before_mention": <true | false>,
    "link_earned": <true | false>,
    "no_business_interest_visible": <true | false>,
    "subreddit_appropriate": <true | false>,
    "would_upvote": <true | false>
  }}
}}

## POST TO ANALYZE
{post}"""


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def analyze_email(request: AnalyzeRequest, http_request: Request):
    ip = http_request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not ip:
        ip = http_request.client.host if http_request.client else "unknown"

    check_rate_limit(ip)

    text = request.email_text.strip()

    # 5. BETTER ERROR MESSAGES
    if len(text) < 50:
        raise HTTPException(400,
            "Too short to analyze. Paste the full cancellation or delay email — "
            "not just the subject line."
        )
    if len(text) > 20000:
        raise HTTPException(400,
            "Email too long. Paste just the main section of your cancellation notice."
        )

    print(json.dumps({
        "event": "analyze_request",
        "ip": ip,
        "session_id": request.session_id,
        "length": len(text),
        "preview": text[:150].replace("\n", " "),
        "timestamp": datetime.now().isoformat(),
    }))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "Server configuration error")

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": ANALYZE_PROMPT.format(
                email_text=text,
                accepted_rebooking=str(request.accepted_rebooking).lower()
            )}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()

        result = json.loads(raw)

        # 5. SMART GUIDANCE FOR WRONG EMAIL TYPE
        if result.get("wrong_email_type") or result.get("email_type") == "booking_confirmation":
            result["eligible"] = False
            result["guidance"] = (
                "This looks like a booking confirmation, not a cancellation notice. "
                "To check for a refund, paste the email with subject line like "
                "'Your flight has been cancelled' or 'Important: flight delay update.' "
                "That's where refund eligibility is determined."
            )

        print(json.dumps({
            "event": "analyze_complete",
            "session_id": request.session_id,
            "eligible": result.get("eligible"),
            "airline": result.get("airline"),
            "refund": result.get("estimated_refund_min"),
            "confidence": result.get("confidence_score"),
            "accepted_rebooking": request.accepted_rebooking,
            "disqualified_by_rebooking": request.accepted_rebooking and not result.get("eligible"),
            "timestamp": datetime.now().isoformat(),
        }))

        # 4. ATTACH RECENT WINS for social proof
        result["recent_wins"] = _get_recent_wins()

        return result

    except json.JSONDecodeError:
        raise HTTPException(500,
            "Couldn't parse this email format. Try pasting just the main body text — "
            "remove any email headers or footer disclaimers and try again."
        )
    except anthropic.RateLimitError:
        raise HTTPException(429, "Service busy — try again in a moment.")
    except Exception as e:
        print(f"Analyze error: {e}")
        raise HTTPException(500, "Something went wrong — try again.")


# ── 2. EMAIL CAPTURE ──────────────────────────────────────────────────────────

@app.post("/capture-email")
async def capture_email(data: EmailCaptureRequest, background_tasks: BackgroundTasks):
    """Non-converter email capture. Your retargeting list."""
    capture = {
        "email": data.email,
        "estimated_refund": data.estimated_refund,
        "airline": data.airline,
        "session_id": data.session_id,
        "source": data.source,
        "confidence_score": data.confidence_score,
        "eligible": data.eligible,
        "flight_number": data.flight_number,
        "accepted_rebooking": data.accepted_rebooking,
        "first_name": data.first_name,
        "last_name": data.last_name,
        "result_state": data.result_state,
        "original_email_text": data.original_email_text or data.free_text_description or None,
        "subject_line": data.subject_line,
        "captured_at": datetime.now().isoformat(),
    }
    email_captures.append(capture)
    print(json.dumps({"event": "email_captured", **capture}))

    if data.source in ("claim_form_submitted", "remind_later_high_confidence") and data.estimated_refund:
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

    if data.source == "something_else":
        try:
            desc_preview = (data.free_text_description or "")[:200]
            resend.Emails.send({
                "from": os.environ.get("FROM_EMAIL", "FlightClaim <claims@flightclaim.today>"),
                "to": [os.environ.get("OPERATOR_EMAIL", "claims@flightclaim.today")],
                "subject": f"Something else intake: {desc_preview[:60]}... — {data.email}",
                "html": f"""
<div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px">
  <h2 style="color:#f59e0b">Something else — needs review</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <tr><td style="padding:8px 0;color:#666;width:100px">Email</td><td style="padding:8px 0;font-weight:600">{data.email}</td></tr>
    <tr><td style="padding:8px 0;color:#666">Name</td><td style="padding:8px 0">{data.first_name or '—'}</td></tr>
    <tr><td style="padding:8px 0;color:#666">Session</td><td style="padding:8px 0">{data.session_id or '—'}</td></tr>
  </table>
  <div style="margin-top:20px;padding:16px;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;font-size:14px;color:#333;line-height:1.7">
    <strong>Their description:</strong><br><br>{data.free_text_description or '—'}
  </div>
</div>""",
            })
        except Exception as e:
            print(f"Something else alert error: {e}")

    if data.source == "claim_form_submitted":
        try:
            operator_html = f"""
            <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px">
              <h2 style="color:#18a362">New claim submitted</h2>
              <table style="width:100%;border-collapse:collapse;font-size:14px">
                <tr><td style="padding:8px 0;color:#666;width:140px">Name</td><td style="padding:8px 0;font-weight:600">{data.first_name} {data.last_name}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Email</td><td style="padding:8px 0">{data.email}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Airline</td><td style="padding:8px 0">{data.airline or '—'}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Flight</td><td style="padding:8px 0">{data.flight_number or '—'}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Refund est.</td><td style="padding:8px 0;color:#18a362;font-weight:700">${data.estimated_refund or '—'}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Confidence</td><td style="padding:8px 0">{round((data.confidence_score or 0) * 100)}%</td></tr>
                <tr><td style="padding:8px 0;color:#666">Accepted rebooking</td><td style="padding:8px 0">{data.accepted_rebooking}</td></tr>
                <tr><td style="padding:8px 0;color:#666">Result state</td><td style="padding:8px 0">{data.result_state or '—'}</td></tr>
              </table>
              {"<div style='margin-top:20px;padding:16px;background:#f5f5f0;border-radius:8px;font-size:13px;color:#333'><strong>Original email:</strong><br><br>" + (data.original_email_text or '—')[:2000] + "</div>" if data.original_email_text else ""}
            </div>"""
            resend.Emails.send({
                "from": os.environ.get("FROM_EMAIL", "FlightClaim <claims@flightclaim.today>"),
                "to": [os.environ.get("OPERATOR_EMAIL", "claims@flightclaim.today")],
                "subject": f"New claim: {data.first_name} {data.last_name} — {data.airline or 'Unknown airline'} — ${data.estimated_refund or '?'}",
                "html": operator_html,
            })
            print(json.dumps({"event": "operator_alert_sent", "email": data.email}))
        except Exception as e:
            print(f"Operator alert error: {e}")

    if os.environ.get("SUPABASE_URL"):
        try:
            from supabase import create_client
            db = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_KEY", "")
            )
            db.table("email_captures").insert(capture).execute()
        except Exception as e:
            print(f"Supabase error: {e}")

    return {"message": "Got it — we'll follow up about your refund."}


@app.get("/operator/captures")
async def get_captures(key: str = ""):
    """View captured emails — your retargeting list."""
    if key != os.environ.get("OPERATOR_SECRET", "changeme"):
        raise HTTPException(403, "Unauthorized")

    return {
        "count": len(email_captures),
        "potential_revenue": round(sum((c.get("estimated_refund") or 0) * 0.20 for c in email_captures), 2),
        "captures": email_captures,
    }


# ── 3. EMAIL ME THIS RESULT ───────────────────────────────────────────────────

@app.post("/email-result")
async def email_result(request: EmailResultRequest, background_tasks: BackgroundTasks):
    """Send the analysis result to the user's email for later action."""
    result = request.result
    refund = result.get("estimated_refund_min") or 0
    you_keep = result.get("user_keeps_estimate") or round(refund * 0.80)

    background_tasks.add_task(_send_result_email, request.email, result, refund, you_keep, request.source or "email_me_result")

    print(json.dumps({
        "event": "result_email_requested",
        "email": request.email,
        "refund": refund,
        "timestamp": datetime.now().isoformat(),
    }))

    if os.environ.get("SUPABASE_URL") and hasattr(request, 'original_email_text'):
        try:
            from supabase import create_client
            db = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_KEY", "")
            )
            db.table("email_captures").update({
                "original_email_text": request.original_email_text
            }).eq("email", request.email).eq("source", "email_me_result").is_("original_email_text", "null").execute()
        except Exception as e:
            print(f"email-result supabase update error: {e}")

    return {"message": f"Result sent to {request.email}"}


async def _send_result_email(email: str, result: dict, refund: float, you_keep: float, source: str = "email_me_result"):
    print(f"EMAIL TASK START: sending to {email}, source={source}")
    print("RESEND KEY PRESENT:", bool(os.environ.get("RESEND_API_KEY")))

    airline = result.get("airline", "the airline")
    reason = result.get("reason", "")
    from_email = os.environ.get("FROM_EMAIL", "FlightClaim <claims@flightclaim.today>")

    if refund is None:
        print(f"EMAIL TASK SKIPPED: refund was None")
        return

    reason_block = f"""
      <div style="background:#f5f5f0;border-radius:8px;padding:16px;margin-bottom:20px">
        <p style="color:#333;font-size:14px;line-height:1.6;margin:0">{reason}</p>
      </div>"""

    keep_block = f"""
      <div style="background:#f9f9f7;border:1px solid #e5e5e0;border-radius:8px;padding:16px;margin-bottom:20px;text-align:center">
        <div style="color:#888;font-size:11px;letter-spacing:0.1em;margin-bottom:4px">YOU KEEP</div>
        <div style="color:#18a362;font-size:32px;font-weight:800">${you_keep:.0f}</div>
        <div style="color:#999;font-size:12px">after our 20% fee · $0 if nothing recovered</div>
      </div>"""

    if source == "claim_form_submitted":
        subject = f"We've received your claim — ${refund:.0f}"
        html = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px;background:#fff">
          <h1 style="font-size:22px;font-weight:800;margin-bottom:4px;color:#0d0d0c">We've received your claim.</h1>
          <p style="color:#666;font-size:14px;margin-bottom:20px">{airline}</p>
          <div style="background:#edf8f2;border:1px solid #a8dfc0;border-radius:8px;padding:14px 16px;margin-bottom:20px">
            <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#117a49;margin-bottom:4px">Estimated refund</div>
            <div style="font-size:28px;font-weight:800;color:#18a362">${refund:.0f}</div>
          </div>
          {reason_block}
          <div style="border-top:1px solid #e5e5e0;padding-top:20px;margin-bottom:20px">
            <p style="font-size:14px;font-weight:700;color:#0d0d0c;margin-bottom:8px">What happens next</p>
            <p style="font-size:14px;color:#444;line-height:1.7;margin:0">We're reviewing your claim and will follow up shortly with next steps. You do not need to take any further action. If we need additional information, we will email you directly.</p>
          </div>
          {keep_block}
          <p style="color:#999;font-size:12px;text-align:center;margin-top:8px">
            FlightClaim is a claims assistance service, not a law firm. Results are not guaranteed.
          </p>
        </div>"""

    elif source == "remind_later_high_confidence":
        subject = f"Your refund estimate is saved — ${refund:.0f}"
        html = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px;background:#fff">
          <h1 style="font-size:22px;font-weight:800;margin-bottom:4px;color:#0d0d0c">Your estimate is saved.</h1>
          <p style="color:#666;font-size:14px;margin-bottom:20px">{airline}</p>
          <div style="background:#edf8f2;border:1px solid #a8dfc0;border-radius:8px;padding:14px 16px;margin-bottom:20px">
            <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#117a49;margin-bottom:4px">Estimated refund</div>
            <div style="font-size:28px;font-weight:800;color:#18a362">${refund:.0f}</div>
          </div>
          {reason_block}
          <div style="border-top:1px solid #e5e5e0;padding-top:20px;margin-bottom:20px">
            <p style="font-size:14px;color:#444;line-height:1.7;margin:0">You have not submitted a claim yet. When you're ready, simply reply to this email and we'll help you move forward.</p>
          </div>
          {keep_block}
          <p style="color:#999;font-size:12px;text-align:center;margin-top:8px">
            FlightClaim is a claims assistance service, not a law firm. Results are not guaranteed.
          </p>
        </div>"""

    else:
        subject = f"Your refund estimate: ${refund:.0f}"
        html = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px;background:#fff">
          <h1 style="font-size:22px;font-weight:800;margin-bottom:4px;color:#0d0d0c">Here's your refund estimate.</h1>
          <p style="color:#666;font-size:14px;margin-bottom:20px">{airline}</p>
          <div style="background:#edf8f2;border:1px solid #a8dfc0;border-radius:8px;padding:14px 16px;margin-bottom:20px">
            <div style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:#117a49;margin-bottom:4px">Estimated refund</div>
            <div style="font-size:28px;font-weight:800;color:#18a362">${refund:.0f}</div>
          </div>
          {reason_block}
          <div style="border-top:1px solid #e5e5e0;padding-top:20px;margin-bottom:20px">
            <p style="font-size:14px;color:#444;line-height:1.7;margin:0">This is an estimate based on the information provided. If you'd like help filing your claim, just reply to this email and we'll take care of it.</p>
          </div>
          {keep_block}
          <p style="color:#999;font-size:12px;text-align:center;margin-top:8px">
            FlightClaim is a claims assistance service, not a law firm. Results are not guaranteed.
          </p>
        </div>"""

    try:
        print(f"EMAIL TASK: calling Resend, subject={subject}")
        response = resend.Emails.send({
            "from": from_email,
            "to": [email],
            "reply_to": [from_email],
            "subject": subject,
            "html": html,
        })
        print(f"EMAIL TASK SUCCESS: {response}")
    except Exception as e:
        print(f"EMAIL TASK ERROR: {repr(e)}")

# ── 4. RECENT WINS ────────────────────────────────────────────────────────────

@app.get("/recent-wins")
async def get_recent_wins_endpoint():
    return {"wins": _get_recent_wins()}


def _get_recent_wins() -> list:
    """
    Return real wins only. Never serve seeded/illustrative data as recent wins.
    The homepage empty state and result panel use different trust signals instead.
    """
    return [w for w in recent_wins_store if not w.get("illustrative")][:3]


@app.post("/operator/add-win")
async def add_win(amount: float, airline: str, key: str = ""):
    """Add a real win — gradually replaces seeded data."""
    if key != os.environ.get("OPERATOR_SECRET", "changeme"):
        raise HTTPException(403, "Unauthorized")

    recent_wins_store.insert(0, {
        "amount": amount,
        "airline": airline,
        "hours_ago": 0,
        "real": True,
    })
    if len(recent_wins_store) > 10:
        recent_wins_store.pop()

    return {"message": f"Win added: ${amount} from {airline}"}


# ── INBOUND EMAIL WEBHOOK ────────────────────────────────────────────────────

@app.post("/inbound")
async def inbound_email(request: Request):
    """Receive inbound emails forwarded by Resend and forward to operator."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    try:
        # Resend inbound wraps data inside a "data" key
        data_obj  = payload.get("data", payload)
        sender    = data_obj.get("from", "") or payload.get("from", "")
        subject   = data_obj.get("subject", "") or payload.get("subject", "") or "(no subject)"
        to_addr   = data_obj.get("to", "") or payload.get("to", "")
        email_id = data_obj.get("email_id", "")
        email_body = ""
        if email_id:
            try:
                api_key = os.environ.get("RESEND_API_KEY", "")
                import httpx
                resp = httpx.get(
                    f"https://api.resend.com/emails/receiving/{email_id}",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
                print(json.dumps({
                    "event": "inbound_fetch_response",
                    "status": resp.status_code,
                    "body_preview": resp.text[:500],
                    "timestamp": datetime.now().isoformat(),
                }))
                if resp.status_code == 200:
                    email_data = resp.json()
                    email_body = email_data.get("text", "") or email_data.get("html", "") or ""
                    email_body = email_body.strip()
                    print(json.dumps({
                        "event": "inbound_email_fetched",
                        "email_id": email_id,
                        "body_length": len(email_body),
                        "timestamp": datetime.now().isoformat(),
                    }))
            except Exception as e:
                print(f"Resend fetch error: {e}")

        print(json.dumps({
            "event": "inbound_email_received",
            "from": sender,
            "subject": subject,
            "body_length": len(email_body),
            "raw_keys": list(payload.keys()),
            "data_keys": list(data_obj.keys()) if isinstance(data_obj, dict) else [],
            "timestamp": datetime.now().isoformat(),
        }))

        display_body = f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{email_body[:4000]}</pre>"

        forward_html = f"""
        <div style="font-family:sans-serif;max-width:640px;margin:0 auto;padding:32px">
          <h2 style="color:#18a362">Inbound reply received</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:24px">
            <tr><td style="padding:6px 0;color:#666;width:80px">From</td><td style="padding:6px 0;font-weight:600">{sender}</td></tr>
            <tr><td style="padding:6px 0;color:#666">To</td><td style="padding:6px 0">{to_addr}</td></tr>
            <tr><td style="padding:6px 0;color:#666">Subject</td><td style="padding:6px 0">{subject}</td></tr>
          </table>
          <hr style="border:none;border-top:1px solid #e5e5e0;margin:0 0 24px">
          {display_body}
        </div>"""

        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "FlightClaim <claims@flightclaim.today>"),
            "to": [os.environ.get("OPERATOR_EMAIL", "claims@flightclaim.today")],
            "subject": f"Reply from {sender}: {subject}",
            "html": forward_html,
        })
        print(json.dumps({"event": "inbound_forwarded", "from": sender}))

        if os.environ.get("SUPABASE_URL"):
            try:
                from supabase import create_client
                db = create_client(
                    os.environ.get("SUPABASE_URL"),
                    os.environ.get("SUPABASE_SERVICE_KEY", "")
                )
                db.table("email_captures").insert({
                    "email": sender,
                    "source": "inbound_email",
                    "subject_line": subject,
                    "original_email_text": email_body[:10000] if email_body else None,
                    "result_state": "inbound_needs_review",
                }).execute()
                print(json.dumps({"event": "inbound_supabase_saved", "from": sender}))
            except Exception as e:
                print(f"Inbound Supabase error: {e}")

    except Exception as e:
        print(f"Inbound email error: {e}")

    return {"ok": True}


# ── RADAR ─────────────────────────────────────────────────────────────────────

@app.post("/radar-analyze")
async def radar_analyze(request: RadarAnalyzeRequest):
    """Operator-only: score a Reddit post and draft replies."""
    secret = os.environ.get("OPERATOR_SECRET")
    if not secret or request.operator_key != secret:
        raise HTTPException(status_code=403, detail="Unauthorized")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Server configuration error")

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": RADAR_PROMPT.format(post=request.post)}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Radar analysis failed — try again")

    print(json.dumps({
        "event": "radar_analyze",
        "score": result.get("score"),
        "recommended_action": result.get("recommended_action"),
        "timestamp": datetime.now().isoformat(),
    }))
    return result


@app.get("/radar")
async def radar_tool(key: Optional[str] = None):
    """Operator-only: serve the Reddit Radar HTML tool."""
    secret = os.environ.get("OPERATOR_SECRET")
    if not secret or key != secret:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=403)
    return FileResponse("radar.html")


# ── HEALTH + SERVE HTML ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}



@app.get("/terms.html")
async def serve_terms():
    return FileResponse("terms.html")


@app.get("/privacy.html")
async def serve_privacy():
    return FileResponse("privacy.html")


@app.get("/")
async def serve_demo():
    try:
        return FileResponse("flightclaim-demo.html")
    except Exception:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;background:#0a0a0a;color:#f5f5f0'>"
            "<h1 style='color:#00e676'>FlightClaim</h1>"
            "<p>flightclaim-demo.html not found in current directory.</p>"
            "</body></html>"
        )
