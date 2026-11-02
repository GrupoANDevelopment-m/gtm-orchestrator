# speed-to-lead-full

End-to-end inbound lead handling: qualify + score + draft + enqueue.

## When to use
Inbound webhook fires (form, Twilio, email parser).

## Tools
- `qualify_inbound`
- `score_lead_icp`
- `draft_reply`

## Example
```
POST /webhooks/inbound {email, message} -> pipeline auto-runs
```
