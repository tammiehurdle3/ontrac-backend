# Bachs multi-merchant setup

The selected merchant account is stored in **Django Admin → Site Settings → Bachs Merchant Account**. Only the account key is stored in the database; API keys and webhook secrets belong in Railway environment variables.

## Configure accounts

Add `BACHS_ACCOUNTS_JSON` as a Railway environment variable containing a JSON object keyed by stable account names. Example shape (replace placeholders with credentials from the corresponding approved Bachs accounts):

```json
{
  "default": {
    "label": "Existing Bachs account",
    "api_key": "<existing-account-api-key>",
    "webhook_secret": "<existing-account-webhook-secret>",
    "api_base_url": "https://sandbox-api.bachs.io"
  },
  "secondary": {
    "label": "Secondary approved account",
    "api_key": "<secondary-account-api-key>",
    "webhook_secret": "<secondary-account-webhook-secret>",
    "api_base_url": "https://sandbox-api.bachs.io"
  }
}
```

Use the API base URL assigned by Bachs for each account/environment. Keep credentials private and do not commit real values. If `BACHS_ACCOUNTS_JSON` is absent, the existing `BACHS_API_KEY`, `BACHS_WEBHOOK_SECRET`, and `BACHS_API_BASE_URL` settings continue to supply the `default` account.

The webhook endpoint remains `/api/webhooks/bachs/`. Configure each merchant account to send its signed webhook events to the deployed endpoint and use that account's matching webhook secret in the JSON configuration. Do not switch to live credentials until the relevant Bachs account and webhook have been tested.

## Switching accounts

1. Deploy the application and apply the database migration.
2. Open Django Admin → Site Settings.
3. Select the named Bachs account and save.
4. New checkout sessions use that account. Existing attempts retain their account key, so a later switch does not change which webhook secret is used to verify an already-started payment.

## Opaque checkout references and return URLs

The reference sent to Bachs is `ontrac_<random hex>` and never contains a tracking ID. The `success_url` and `cancel_url` point at the frontend route `/checkout/return/<signed token>`; the token is a signature over the local `BachsPaymentAttempt` primary key and reveals no shipment data. The frontend resolves it through `GET /api/bachs/return/<token>/` and only then navigates to `/checkout/<tracking id>`.

Sessions created before this change keep the legacy `ontrac_<tracking id>_<random>` reference. Those references are still verified by their exact stored value, and the tracking-ID prefix check only runs when no local attempt exists.

Deploy the frontend build that contains the `/checkout/return/:returnToken` route **before** the backend build that starts emitting these URLs.

The customer-facing checkout route and USD conversion remain unchanged. Payment confirmation requires a valid signed webhook and the locally stored payment attempt or the legacy metadata path. Metadata omission should only be enabled for an account after confirming the Bachs API supports it and verifying that the event still provides a reliable reference/checkout ID for reconcilation.
