# API keys and outgoing webhooks

This guide is for people connecting another system to Aurora. Administrators create keys and webhook endpoints
under **Administration → API and webhooks**. Design notes are in ADR-043.

## API keys

Send the key as a bearer token to the API (not to the web app):

```
curl -H "Authorization: Bearer aur_1a2b3c4d_..." https://api.example.com/products?limit=50
```

- A key has a role: viewer (read only), operator (also uploads, stock and purchasing) or analyst (also training,
  signals and overrides). It never has more rights than the administrator who created it has now, and it stops
  working if that person is deactivated or deleted.
- Keys cannot reach sign-in, account, user, API key, data export or organisation deletion endpoints. Those answer
  `403` with code `api_key_not_allowed`.
- Changes made with a key are recorded in the audit log under the creating administrator, with the key's prefix
  (`aur_1a2b3c4d`) in the details.
- An invalid, expired or revoked key gets `401 Invalid or revoked API key`. Each key shares the per-minute request
  limit of a signed-in user (`API_RATE_LIMIT_PER_MINUTE`).
- The full key is shown once. Aurora stores only its SHA-256, so a lost key cannot be recovered; revoke it and
  create a new one.
- The interactive reference for every endpoint is at `/docs` on the API (see README).
- The calling system must be able to reach the API itself. In the Docker Compose setup the API is published only on
  the host (port 8100); put it behind your reverse proxy with TLS before giving keys to systems elsewhere (RUNBOOK
  section 3).

## Outgoing webhooks

Aurora sends an HTTP `POST` with a JSON body to your HTTPS address when an event you chose happens.

| Event | When |
|---|---|
| `alert.opened` | A new alert is raised. An alert that is already open and gets worse is not a new event |
| `purchase_order.status_changed` | An order is approved, sent, received (fully or partly) or cancelled |
| `job.finished` | A background job (training, batch forecast, data export and others) succeeds or fails |
| `model.promoted` | A model version becomes the active one, including a rollback |
| `ping` | You pressed *Send test* |

Body (comments added here for explanation):

```jsonc
{
  "id": "5f0c...",                 // delivery id, also in the Aurora-Delivery header; use it to ignore repeats
  "type": "purchase_order.status_changed",
  "created_at": "2026-10-27T10:15:00+00:00",
  "data": {"id": "...", "number": "PO-000042", "status": "sent", "previous_status": "approved", "...": "..."}
}
```

Headers: `Aurora-Event`, `Aurora-Delivery`, `Aurora-Signature`, `Content-Type: application/json`.

### Checking the signature

`Aurora-Signature` looks like `t=1767225600,v1=5d41...`. `v1` is the hex HMAC-SHA256 of
`"<t>." + <raw request body>` with your endpoint's signing secret (`whsec_...`). Compare it in constant time and
reject requests whose `t` is more than 5 minutes from your clock.

Python:

```python
import hashlib, hmac, time

def valid(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    fields = dict(p.split("=", 1) for p in header.split(","))
    t = int(fields["t"])
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, fields.get("v1", ""))
```

Node.js:

```js
import crypto from "node:crypto";

export function valid(secret, header, rawBody, tolerance = 300) {
  const fields = Object.fromEntries(header.split(",").map((p) => p.split("=", 2)));
  const t = Number(fields.t);
  if (!Number.isFinite(t) || Math.abs(Date.now() / 1000 - t) > tolerance) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${t}.`).update(rawBody).digest("hex");
  const got = Buffer.from(fields.v1 ?? "", "utf8");
  return got.length === expected.length && crypto.timingSafeEqual(got, Buffer.from(expected, "utf8"));
}
```

Verify against the raw bytes you received, before parsing the JSON.

### Delivery rules

- Answer with any `2xx` status within 10 seconds. Redirects are not followed and count as failures.
- Failed deliveries are retried after 1 minute, 5 minutes, 30 minutes, 2 hours and 12 hours (6 attempts, about
  15 hours). After that the delivery is marked failed; an administrator can retry it from the page.
- The same delivery can arrive more than once (for example after a timeout on your side). Use the delivery id to
  ignore repeats. Order is not guaranteed.
- After 30 failed attempts in a row the endpoint is switched off and shows why. Turning it back on resets the count.
- Addresses must be public HTTPS on port 443. Aurora checks the address when it is saved and again before every
  request, and refuses private and reserved networks.
- *Rotate secret* replaces the signing secret at once; update your receiver at the same time.
- Delivery history is kept for 30 days.
