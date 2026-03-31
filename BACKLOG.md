# FlightClaim — Backlog

Items marked `[ ]` are pending. Mark `[x]` when done. Add new items with `log later`.

---

## 🔴 Must Fix (Pre-Scale)

- [ ] Change `OPERATOR_SECRET` in Railway from `changeme` to something secure
- [ ] Fix `original_email_text` null on `email_me_result` and `remind_later` captures — text not saving to Supabase on those paths despite being in sendCapture()

## 🟡 Soon (Post-Launch)

- [ ] Stripe integration — charge 20% fee when refund confirmed
- [ ] Email follow-up sequences — follow up with non-converters automatically
- [ ] Verify `FROM_EMAIL` env var is set correctly in Railway (fallback still shows onboarding@resend.dev in _send_result_email)

## 🟢 Later / Nice to Have

- [ ] Separate `claims` table distinct from `email_captures` for cleaner ops
- [ ] Automated claim filing — replace manual follow-up
- [ ] DOT complaint generation — auto-draft if airline doesn't respond
- [ ] Ops status fields on claims — new / filed / waiting / won / lost
- [ ] Simple operator dashboard — filter Supabase by status without SQL

## ✅ Done

- [x] RLS enabled on email_captures (service role retains full access)
- [x] Inbound email pipeline — claims@flightclaim.today → Supabase + operator alert
- [x] Operator alert on claim submission
- [x] original_email_text stored on claim_form_submitted
- [x] Cloudflare DNS migration
- [x] Custom domain live (flightclaim.today)
- [x] Terms + Privacy pages
- [x] Supabase capture on all paths
