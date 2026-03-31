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
| Database | Supabase (project: GiftClaim, region: us-east-1) |
| DNS | Cloudflare → flightclaim.today |
| Domain | Namecheap → flightclaim.today |

## Railway Environment Variables
- `ANTHROPIC_API_KEY`
- `RESEND_API_KEY` — full access key (required for inbound email fetch)
- `FROM_EMAIL` = claims@flightclaim.today
- `FRONTEND_URL` = https://flightclaim.today
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `OPERATOR_SECRET` = changeme (change before scaling)
- `OPERATOR_EMAIL` = your personal email for claim alerts

## Backend Endpoints
- `POST /analyze` — AI email analysis
- `POST /capture-email` — saves lead to Supabase
- `POST /email-result` — sends result email via Resend
- `POST /inbound` — Resend inbound webhook (receives forwarded emails)
- `GET /health` — Railway health check
- `GET /operator/captures?key=changeme` — view captured leads (in-memory)
- `GET /recent-wins` — returns real wins for social proof
- `POST /operator/add-win?key=changeme` — add a real win
- `GET /`, `/terms.html`, `/privacy.html` — serve static files

## Supabase Table: email_captures
| Column | Type | Notes |
|---|---|---|
| id | uuid | auto-generated |
| email | text | required |
| estimated_refund | float | nullable |
| airline | text | nullable |
| session_id | text | nullable |
| source | text | see source values below |
| captured_at | timestamp | auto |
| confidence_score | float | nullable |
| eligible | boolean | nullable |
| flight_number | text | nullable |
| accepted_rebooking | boolean | nullable |
| first_name | text | nullable |
| last_name | text | nullable |
| result_state | text | see result states below |
| original_email_text | text | full pasted or forwarded email |
| notes | text | manual operator notes |
| subject_line | text | inbound email subject |

Note: RLS is currently disabled — enable before scaling to production traffic.

## Source Values (analytics)
- `claim_form_submitted` — user completed claim form
- `email_me_result` — clicked "Email me this result"
- `remind_later_high_confidence` — clicked "Remind me later"
- `email_capture_medium_confidence` — medium confidence inline capture
- `email_capture_not_eligible` — not eligible soft capture
- `inbound_email` — forwarded email to claims@flightclaim.today
- `something_else` — user submitted a free-form description of a non-standard issue

## Result States (analytics)
- `eligible_high_confidence` (≥85%)
- `eligible_medium_confidence` (70-84%)
- `eligible_low_confidence` (65-69%)
- `wrong_email_type`
- `not_eligible`
- `inbound_needs_review` — forwarded email, needs manual review
- `something_else_intake` — free-form issue submitted, pending manual operator review

## Inbound Email Flow
1. User forwards airline email to claims@flightclaim.today
2. Resend MX record (Cloudflare DNS) receives it
3. Resend webhook POSTs to /inbound on Railway
4. Server fetches full email body via GET /emails/receiving/{email_id}
5. Forwards full email to OPERATOR_EMAIL
6. Saves row to Supabase with source=inbound_email
7. Operator reviews and follows up manually

## What Is Live ✅
- Analysis flow end to end
- Email sending via Resend
- Supabase capture on all paths
- Operator alert on claim submission (email to OPERATOR_EMAIL)
- Original email text stored on all captures
- Inbound email forwarding (claims@flightclaim.today)
- Custom domain + Cloudflare DNS
- Terms + Privacy pages
- Something Else intake (Entry Point A: always visible; Entry Point B: post not-eligible result)

## What Is NOT Live Yet
- Automated claim filing (manual follow-up for now)
- Stripe / payment collection
- Email follow-up sequences
- RLS on Supabase

## Operator Workflow (current)
1. User submits claim → operator alert email sent instantly with full details
2. User forwards email to claims@flightclaim.today → row in Supabase + operator forwarded
3. All leads visible in Supabase → filter by source = claim_form_submitted for active queue
4. Follow up manually by email
5. Use notes column in Supabase to track claim status

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
