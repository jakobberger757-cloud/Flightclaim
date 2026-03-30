# FlightClaim — System Reference

## Product
FlightClaim helps users recover airline cash refunds from cancellations and delays.
Users paste an airline email → AI checks DOT eligibility → user submits a claim.
We charge 20% of recovered refunds. $0 if nothing recovered.

## Live URL
https://flightclaim.today

## Stack
| Layer | Tool |
|---|---|
| Frontend | flightclaim-demo.html (single HTML file) |
| Backend | proxy.py (FastAPI) |
| AI | Anthropic Claude API (claude-sonnet-4-20250514) |
| Hosting | Railway (Docker, port 8080) |
| Email | Resend (claims@flightclaim.today) |
| Database | Supabase |
| Domain | Namecheap → flightclaim.today |

## Railway Environment Variables
- `ANTHROPIC_API_KEY`
- `RESEND_API_KEY`
- `FROM_EMAIL` = claims@flightclaim.today
- `FRONTEND_URL` = https://flightclaim.today
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `OPERATOR_SECRET` = changeme

## Backend Endpoints
- `POST /analyze` — AI email analysis
- `POST /capture-email` — saves lead to Supabase
- `POST /email-result` — sends result email via Resend
- `GET /health` — Railway health check
- `GET /operator/captures?key=changeme` — view captured leads
- `GET /`, `/terms.html`, `/privacy.html` — serve static files

## Supabase Table: email_captures
email, estimated_refund, airline, session_id, source, confidence_score,
eligible, flight_number, accepted_rebooking, first_name, last_name, result_state, captured_at

## Source Values (analytics)
- `claim_form_submitted` — user completed claim form
- `email_me_result` — clicked "Email me this result"
- `remind_later_high_confidence` — clicked "Remind me later"
- `email_capture_medium_confidence` — medium confidence inline capture
- `email_capture_not_eligible` — not eligible soft capture

## Result States (analytics)
- `eligible_high_confidence` (≥85%)
- `eligible_medium_confidence` (70-84%)
- `eligible_low_confidence` (65-69%)
- `wrong_email_type`
- `not_eligible`

## What Is Live
- Analysis flow end to end
- Email sending (Resend) ✅
- Supabase capture ✅
- Custom domain ✅
- Terms + Privacy pages ✅

## What Is NOT Live Yet
- Automated claim filing (manual follow-up for now)
- Stripe / payment collection
- Inbound email forwarding
- Email follow-up sequences

## Operator Workflow (current)
1. User submits claim → row appears in Supabase
2. Operator (you) follows up manually by email
3. No automated filing yet

## Key Product Rules
- Accepted rebooking → NOT eligible (shown post-analysis, not pre)
- Never guarantee refunds — always "appears eligible"
- Not a law firm
- 20% fee disclosed everywhere
- Not affiliated with airlines or DOT

## Priorities
1. Get first real users (Reddit)
2. Handle first claims manually
3. Confirm airlines actually pay out
4. Add Stripe once validated
5. Automate filing after that
