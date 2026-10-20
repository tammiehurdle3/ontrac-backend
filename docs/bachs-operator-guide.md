# Bachs payments — operator guide

Read this before touching Bachs configuration. It is written so a new operator or
agent can continue without reading the whole codebase.

## Where things live

| What | Where |
|---|---|
| Merchant API keys and webhook secrets | Encrypted in the database. Edit at **Django Admin → Bachs merchant accounts** |
| Encryption key for those credentials | Railway variable `BACHS_CREDENTIALS_KEY` (Fernet key, never in the repo) |
| Active merchant | **Django Admin → Site Settings → Bachs Merchant Account** |
| Per-merchant return domain | Admin field `frontend_url` (blank = `BACHS_FRONTEND_URL`) |
| Per-merchant webhook host | Admin field `webhook_host` (e.g. `hooks.milani-cosmetics.com`) |
| Webhook endpoint path | `/api/webhooks/bachs/` on the backend |
| Legacy env accounts | Railway `BACHS_ACCOUNTS_JSON` (an admin account with the same key wins) |

## One payment, end to end

1. Customer opens `/checkout/<trackingId>` on the OnTrac frontend.
2. Frontend calls `POST /api/initiate-bachs/<trackingId>/`.
3. Backend reads the active merchant, creates a `BachsPaymentAttempt` with an
   opaque reference (`ontrac_<random>`), then calls Bachs with that merchant's
   API key.
4. `success_url` / `cancel_url` point at
   `<frontend_url>/checkout/return/<signed token>?bachs_return=...`.
5. Customer pays on the Bachs hosted page.
6. Bachs redirects to the signed token; the frontend resolves it through
   `GET /api/bachs/return/<token>/` and lands on `/checkout/<trackingId>`.
7. Bachs posts `collection.succeeded` to `/api/webhooks/bachs/` on the host
   registered for that merchant.
8. Backend finds the attempt by reference/checkout ID, verifies the signature
   with **that merchant's** webhook secret, marks the shipment paid, shows the
   receipt and pushes a Pusher update.
9. Customer sees the OnTrac confirmation page.

Nothing sent to Bachs contains the tracking ID: not the reference, the return
URLs, the referrer, or (with metadata off) the payload.

## Adding a merchant

1. Get the account's API key and webhook secret from Bachs.
2. Point the webhook hostname at this backend (CNAME / Railway custom domain).
   You can reuse an existing host instead of creating one per merchant.
3. **Django Admin → Bachs merchant accounts → Add**: key, label, API base URL,
   return domain, webhook host, API key, webhook secret. Leave metadata off.
4. In the Bachs dashboard for that account, set the webhook URL to
   `https://<webhook_host>/api/webhooks/bachs/`.
5. **Site Settings → Bachs Merchant Account**: select it.

No Railway change is needed once `BACHS_CREDENTIALS_KEY` exists. The middleware
accepts the hostname stored on the account, so new webhook hosts do not need
`EXTRA_ALLOWED_HOSTS`.

## Switching accounts

New checkouts use the selected account. Existing attempts keep the account key
they were created with, so switching does not break in-flight payments.

## Testing

- **Local**: Bachs rejects non-public return URLs, so set
  `BACHS_OMIT_RETURN_URLS=1` and forward events with the Bachs CLI
  (`bachs listen --forward-to http://localhost:8000/api/webhooks/bachs`).
  The CLI prints a session signing secret; use it as the merchant webhook
  secret. No tunnel and no dashboard endpoint are needed. Return to the
  checkout page manually after paying.
- **Sandbox**: API base `https://sandbox-api.bachs.io`, sandbox test card.
- **Pass criteria**: attempt `succeeded`, webhook `200`, shipment
  `Payment Confirmed`, receipt visible, no tracking ID in anything Bachs saw.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 DisallowedHost` on webhook | host not in `ALLOWED_HOSTS` and not any merchant's `webhook_host` |
| `401 Invalid signature` | wrong webhook secret for the account that created the attempt |
| `503 Webhook not configured` | missing secret, or `BACHS_CREDENTIALS_KEY` cannot decrypt it |
| `502` on checkout start | account missing API key, or credentials cannot be decrypted |
| Return URL 404s | frontend build without `/checkout/return/:returnToken` |
| Payment lands on wrong account | Site Settings selection |

## Deploy order

Frontend first (the return route is additive and harmless on its own), then
backend, then apply migrations. Migrations `0030`–`0032` are additive only.
Never switch a live account until a signed sandbox webhook has been verified.
