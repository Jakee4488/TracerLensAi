# Access control, token quota, and the admin dashboard

Tracer Lens is a beta prototype on a public portfolio site. Before this
existed, `POST /analyze-prompt` was open to anyone: a full causal run against
Vertex AI Agent Engine, no identity, no ceiling. This document covers the gate
that replaced that, the quota it enforces, and the runbook for operating it.

Code: [`proxy/access.py`](../proxy/access.py) (identity, store, gate, notifier,
metrics) and [`proxy/admin.py`](../proxy/admin.py) (2FA, review endpoints,
dashboard). The agent tier (`src/`) is untouched — it only ever sees an opaque
`user_id`.

---

## How a visitor gets in

```
Header "Login" → modal, enter email → POST /auth/login
   ├─ no record   → create pending, email the admin      → "Access requested"
   ├─ pending     → (no re-notify inside the cooldown)   → "Waiting for approval"
   ├─ denied      → polite decline
   └─ approved    → email a signed sign-in link          → "Check your inbox"
                       ↓ visitor clicks
                    ${APP_URL}/?auth=<signed>  →  POST /auth/exchange
                       ↓
                    24-hour session token in sessionStorage, URL param stripped
```

One email field does both jobs — the record decides whether a submission is a
sign-in or an access request. Approving someone emails them the sign-in link
directly, so it is approve → click → in.

### Trust model

Access is bound to **control of the inbox**, not to a typed string. The address
a visitor types only ever *starts* a request; it never grants anything. What
grants access is a signed token that arrived by email.

| Token | Payload | Lifetime | Notes |
|---|---|---|---|
| Sign-in link | `{email, nonce}` | 15 min | Single use — the nonce is cleared on exchange, so a forwarded link is dead |
| Session | `{email, ver}` | 24 h | Rides on `Authorization: Bearer`; `ver` is checked against `token_version`. Held in `sessionStorage`, so closing the browser also ends it |
| Admin one-click | `{action, email}` | 7 days | The Approve/Deny/Grant links in notification emails |
| Admin session | `{admin: true}` | 12 h | Issued only after password **and** OTP |

All four are HMAC-SHA256 over base64url JSON, signed with
`ACCESS_SIGNING_SECRET` (stdlib `hmac` — no new dependency). Every signature
carries a **purpose**, so a sign-in link cannot be replayed as a session and an
admin session cannot be replayed as a one-click link.

Bumping `token_version` (on deny, or on delete) revokes every live session for
that address immediately, rather than waiting out the 24 hours.

**Worth knowing:** this trades Google's OAuth verification for email-link
possession — the same factor Slack, Notion, and Substack use. It is far
stronger than what it replaced (no auth at all on the agent), but a compromised
inbox is now sufficient.

---

## Data model

Two new top-level Firestore collections in the existing `tracerlensai`
database. Top-level, not under `users/{uid}`, because the gate must exist
before there is a session.

### `agent_access/{sha256(email)[:32]}`

| Field | Purpose |
|---|---|
| `email` | Normalized lowercase — needed to email them |
| `status` | `pending` \| `approved` \| `denied` |
| `token_version` | Bumped to revoke live sessions |
| `login_nonce` | Single-use sign-in link |
| `tokens_used` / `token_limit` | The quota |
| `extension_status` / `extension_message` | A pending request for more |
| `runs_total` / `runs_failed` / `latency_ms_sum` / `last_run_at` | Dashboard aggregates |
| `notify_state` / `notify_error` / `notify_attempts` / `notified_at` | Delivery health |
| `requested_at` / `decided_at` / `last_seen` | Timestamps |

The doc id is a hash for id-safety and light pseudonymisation; the plaintext
address is a field because you cannot email a hash. **No IP addresses, no
prompt text, no user agents** — the privacy notice promises exactly that, and
`test_access_record_holds_only_what_the_notice_promises` asserts it.

### `agent_runs/{auto}` — dashboard telemetry

`email_hash`, `ts`, `ok`, `error_kind`, `latency_ms`, `tokens_in`,
`tokens_out`, `tokens_total`, `model`, `causal`, `web`, `expires_at`.

**No prompt or response text, ever.** That is what lets these rows outlive the
24-hour chat retention without contradicting it.

`error_kind` is one of `upstream_http`, `stream_failed`, `proxy_exception`,
`assembly_failed` — which is what makes the dashboard's failure column
actionable rather than a single opaque count.

---

## The quota

- Every approved address starts at `ACCESS_TOKEN_LIMIT` (200 000).
- Usage is incremented once per turn from the actual `total_token_count`
  reported by Agent Engine — which already includes input, output, and
  reasoning tokens. The input/output split is recorded alongside for the
  dashboard.
- At the cap, `/analyze-prompt` returns **403** with
  `{"detail": {"code": "limit_reached", "usage", "limit"}}`, and the UI shows
  the extension-request modal instead of an error bubble.
- Approving an extension **adds** `ACCESS_TOKEN_GRANT` (200 000) to the current
  cap. It is additive, not a reset: someone who burned 250K of a 400K cap keeps
  that usage and gets a 600K ceiling. The new cap takes effect immediately — no
  re-login.

**Known gap:** if a visitor aborts mid-stream the generator closes and tokens
already spent upstream go unrecorded. Bounded by one turn.

---

## Admin

### Signing in

`/admin` is served as self-contained HTML straight from the proxy — no build
step, and no route added to a deliberately router-less SPA.

1. Enter `ADMIN_TOKEN`.
2. A 6-digit code is emailed to `ACCESS_NOTIFY_EMAIL` (10-minute expiry, five
   attempts, 30-second cooldown between requests).
3. Enter it for a 12-hour admin session, held in `sessionStorage`.

**The one-click links in notification emails are exempt from the OTP.** They
are signed, expiring, and single-purpose, and reaching one already requires
access to that inbox — the same second factor an OTP proves, at the cost of the
one click that makes them worth having.

### The dashboard

| Tab | Shows |
|---|---|
| **Requests** | Pending access and pending extensions — Approve / Deny / Grant +200K |
| **Users** | Status, tokens used vs limit, runs, failures, failure rate, avg latency, last seen, Delete |
| **Activity** | Recent turns, failures highlighted, with latency and token split |
| **Alerts** | Failed notifications (with Retry) and requests pending over 24 h |

### Endpoints

| Method | Path | Auth |
|---|---|---|
| `POST` | `/admin/auth/start` · `/admin/auth/verify` | password → OTP |
| `GET` | `/admin/users` · `/admin/runs` · `/admin/pending-count` | admin session |
| `POST` | `/admin/access/approve` · `/admin/access/deny` · `/admin/extension/approve` | admin session |
| `POST` | `/admin/user/delete` · `/admin/notify/retry` · `/admin/sweep` | admin session |
| `GET` | `/admin/act?t=<signed>` | the signed link itself |

---

## If a notification fails

A dropped email should delay a decision, never lose a request. Five layers:

1. **The dashboard is the source of truth, not your inbox.** Every pending
   request appears at `/admin` whether or not any email was delivered.
2. **Failures are recorded**, in `notify_state` / `notify_error` /
   `notify_attempts`, and shown in the dashboard's red alert strip.
3. **Auto-retry.** A visitor's `/access/status` poll retries their own failed
   notification; the dashboard's Retry button retries all of them. Bounded by
   `NOTIFY_MAX_ATTEMPTS` (5).
4. **Structured logs.** A failed send prints `ERROR event=notify_failed …` to
   stdout → Cloud Logging. Attach a log-based alert for a push notification.
5. **Heartbeat (optional, not wired up).** `GET /admin/pending-count` returns
   `{pending, oldest_age_s}`. Pointing the existing `uptime.yml` schedule at it
   and failing when `oldest_age_s > 86400` would surface stale requests through
   GitHub notifications. Say the word and it's a five-line change.

---

## Retention and UK GDPR

### What the modal promises, and what enforces it

> **Your data.** I store your **email address** and a **token-usage counter** —
> that's what approves access and enforces the quota. Kept while you have
> access, deleted whenever you ask. Lawful basis: legitimate interests in
> running and protecting a personally-funded prototype.
>
> **Your chats.** Conversations are **automatically deleted from the server
> within 24 hours** and are never used for training. **Please don't paste
> sensitive or personal information** — while a chat is live it's processed by
> Google Cloud (Vertex AI) to produce the answer.
>
> Your address goes to Resend solely to send you these emails. It isn't shared
> with anyone else, used for marketing, or added to any list.

| Promise | Enforced by |
|---|---|
| Chats gone within 24 h | `expires_at` on every conversation and message (`_save_exchange`), a Firestore TTL policy, **and** `POST /admin/sweep` as the backstop |
| Uploads gone with them | `expires_at` on each upload record; `_get_upload` refuses and drops expired ones |
| Agent-side state gone too | Best-effort session delete against Agent Engine during the sweep |
| Erasure on request | *Delete my data* in the profile menu (`DELETE /account`), or Delete on the dashboard |
| Minimal collection | The schema above is the complete set; a test asserts no IP/prompt/user-agent field creeps in |

The sweep exists because a Firestore TTL policy deletes *typically within 24
hours of expiry* — which alone would make a flat "within 24 hours" claim false.

**Consequence:** the sidebar is a rolling 24-hour window, not an archive. Older
conversations disappear by design, and the sidebar says so.

**Session delete — verified against the API contract.** The REST surface does
support delete. ADK's own `VertexAiSessionService.delete_session` issues
`DELETE reasoningEngines/{engine}/sessions/{session_id}`, which is the exact
path `admin.py:_delete_agent_session` builds. Two details worth knowing:

- **Sessions are addressed by session id alone** — `user_id` is *not* a path
  segment. Passing it would break the call. ADK's own delete re-reads the
  session and compares `user_id` purely as an ownership guard; the sweep does
  not need one, since it only ever deletes conversations it just removed from
  that user's own subtree.
- **`chat_id` really is the session id.** The UI mints `chat-<ts>-<hex>`, which
  satisfies Vertex's `^[A-Za-z0-9_-]+$` session-id rule, so it is honoured as
  the resource id rather than being replaced by a server-assigned one.

Still worth one live check before launch: the path is confirmed from the client
contract, not yet exercised against the real endpoint.

**Known gap — orphaned agent sessions.** The sweep can only delete sessions it
can find, and it finds them by walking Firestore conversations. A turn that
reaches the agent *without* a `chat_id` gets a throwaway upstream session
(`main.py`: `req.chat_id or uuid.uuid4().hex`) while `_persist_if_signed_in`
returns early, so no Firestore document is ever written and the sweep never
learns the id. Those sessions hold `causal_*` state and are not currently
deleted by anything — `create_session` is called without a `ttl`, so Vertex
does not expire them either. Options: have the proxy always send a `chat_id`,
or pass a `ttl` on session creation so Vertex expires them regardless.

### Setting the Firestore TTL policies

```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=conversations --database=tracerlensai --enable-ttl
gcloud firestore fields ttls update expires_at \
  --collection-group=messages --database=tracerlensai --enable-ttl
gcloud firestore fields ttls update expires_at \
  --collection-group=agent_runs --database=tracerlensai --enable-ttl
```

---

## Configuration

See [`.env.example`](../.env.example) for the annotated list. The two that
matter most:

- **`ACCESS_SIGNING_SECRET`** — unset means an ephemeral per-process key, so
  every Cloud Run cold start signs everyone out. Rotating it deliberately is
  how you sign everyone out at once.
- **`ADMIN_TOKEN`** — unset means `/admin` answers 503.

`deploy_to_gcp.sh` forwards each variable to Cloud Run only when non-empty, so
a partial deploy never blanks a value already set on the service.

---

## Local development

`docker compose up` with `MODE=mock` needs **no credentials at all**:

- `ACCESS_STORE=memory` keeps access records in a process-local dict
  ([`proxy/memstore.py`](../proxy/memstore.py)). Set automatically by
  `docker/local-entrypoint.sh`; **never set it in production**.
- `RESEND_API_KEY` unset means every email is printed to the console instead of
  sent — including the approve link and the sign-in link, so you can walk the
  whole flow from the logs.
- The admin password defaults to `local-admin`, and the OTP prints to the same
  console.

Both are deliberately loud: each prints a warning on use.

---

## Tests

| Suite | Covers |
|---|---|
| `tests/test_access.py` (52) | Login branches, single-use and expiring links, purpose confusion, revocation, every gate refusal, address validation, usage accounting, run metrics, extensions, erasure, notification retry, the data-minimisation invariant |
| `tests/test_admin.py` (36) | OTP issue/verify/lockout/expiry, admin session required on every endpoint, one-click link tampering and expiry, dashboard stats and injection, the retention sweep |
| `tests/ui_tests/test_access_gate.py` (13) | Composer locked, gate undismissable, backend refuses signed-out callers, privacy notice rendered, request → approve → sign-in link → in, link replay, denial, profile menu, extension request, logout, admin 2FA |

The E2E suite runs with the gate **on**. Disabling a security feature to keep
tests green would make the suite lie about what ships; instead the server runs
with the in-memory store and a pinned signing secret, and tests read real links
out of the printed-email log. Mark a test `@pytest.mark.logged_out` to opt out
of the auto sign-in.
