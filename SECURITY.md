# Security policy

## Supported versions

Only the `main` branch is supported. Fixes are made there and deployed from there; there are no maintained release branches.

## Reporting a vulnerability

Please report security problems privately, not in a public issue or pull request:

1. Open https://github.com/alihdrndm/eingang/security/advisories/new (repository → **Security** → **Report a vulnerability**).
2. Describe the problem, how to reproduce it, and what an attacker could do with it. Use fictional data in examples; do not send real invoices or personal data.

You will get an answer in the advisory. Please give us a reasonable time to fix the problem before you disclose it.

The public demo contains only fictional sample data and whatever visitors upload into their own sandbox. Do not test against other visitors' data, and do not run load tests or automated scanners against the demo; its rate limits and budgets are small on purpose.

## Personal data the application handles

| Data | Where it is stored | Notes |
|------|--------------------|-------|
| Users: email address, name, password hash, role, organisation | Postgres (`users`) | Passwords are stored only as Django password hashes. Sandbox visitors get a generated address `sandbox-<id>@example.invalid` and an unusable password. |
| Sessions | Postgres (Django sessions) | Session cookie `HttpOnly`, `SameSite=Lax`, `Secure` in production; 12 hours, or until the sandbox expires. |
| Organisations: name, VAT ID | Postgres (`organizations`) | |
| Uploaded and emailed files (PDF, XML): original bytes, original file name, size, SHA-256, sender address for emailed files | Files: local disk in development, a private Supabase Storage bucket in production. Metadata: Postgres (`documents`). | Object keys contain only IDs and the SHA-256 (`orgs/<org_id>/documents/<document_id>/<sha256>.<ext>`), never the file name. Derived files (extracted text, invoice XML, rendered HTML) are stored next to the original. |
| Invoice data: seller and buyer names, postal addresses, email addresses, VAT IDs and tax numbers, IBAN and BIC, amounts, line descriptions | Postgres (`invoices`, `invoice_lines`) | Invoices from sole traders can identify a person. |
| Supplier history: supplier names, VAT IDs, IBANs with first and last sighting and who confirmed a change | Postgres (`suppliers`, `supplier_ibans`) | |
| Review history: approval decisions and comments, check resolution notes, events | Postgres (`approvals`, `checks`, `events`) | The event for an edited field records "changed" instead of the old and new value when the field is an IBAN. |
| Exports (CSV and ZIP files for the accountant) | Storage, next to the documents; metadata in Postgres (`export_batches`) | |
| LLM ledger | Postgres (`llm_calls`) | Purpose, model, token counts, cost, status. Never the prompt or the response text. |
| LLM response cache | Postgres (`llm_cache`) | The structured answer (for an extraction, the field values the model read), keyed by a hash of the request. Rows are kept when a sandbox or document is deleted. |

**Retention.** A sandbox expires `SANDBOX_TTL_HOURS` (default 24) after it was created. While the services run, the daily maintenance workflow (03:10 UTC) deletes every expired sandbox: its database rows and its stored files. Deleting a document in the app is a soft delete: the document is hidden but its rows and files are kept. Organisations that are not sandboxes are kept until an operator deletes them. While the public demo is paused, maintenance does not run, and expired sandboxes are deleted at the first daily run after it resumes.

**Logs** contain one line per request (method, path without the query string, status, duration, request id) and identifiers (UUIDs). They never contain request or response bodies, file contents, names of people, email addresses, IBANs, prompts or model output.

**Mailbox intake** is optional and off unless `MAILBOX_ENABLED=true`. When on, the worker reads the unseen messages of one mailbox over IMAP with TLS and stores their PDF and XML attachments with the sender's address. Nothing else from the message is stored.

## How uploads are handled

- **Size first:** a file larger than `MAX_UPLOAD_BYTES` (default 4 MB) is refused (`413 FILE_TOO_LARGE`) without reading it into memory beyond the limit. At most 10 files per request.
- **Type by content, never by file name or browser content type:** a PDF must start with `%PDF-`; an XML file must be well-formed with a UBL or CII root element. Anything else is refused (`415 UNSUPPORTED_FILE`).
- **XML safety:** every XML parse disables entity resolution, DTD loading and network access. Any XML that contains a DOCTYPE is refused, at upload and when the worker reads the XML embedded in a PDF. The validator and the visualisation only ever receive XML re-serialised from that safe parse, never the original bytes.
- **Serving files:** originals, invoice XML and extracted text are sent as attachments with `Content-Security-Policy: sandbox; default-src 'none'` and `X-Content-Type-Options: nosniff`, so nothing uploaded is rendered as a page from the app's origin. The rendered invoice view is stored with all scripts removed, served with a policy that allows only inline styles and `data:` images, and shown in an `<iframe sandbox="">`.
- **Quotas:** sandboxes have a per-sandbox file limit and a storage budget for all sandboxes together; uploads are rate-limited per user.

## Other protections

- Session authentication with CSRF protection on every unsafe request, including sign-in and opening a sandbox.
- Every query is scoped to the signed-in user's organisation; a test checks every object endpoint against access from another organisation.
- Rate limits per client IP (general, sign-in, sandbox creation) and per user (uploads). Requests sent straight to the Railway API domain are keyed on the client address in `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`). Requests through the Vercel site are keyed on the address a Vercel middleware forwards together with a shared secret; without the matching secret that header is ignored, so a client cannot choose its own key either way (`docs/DEPLOY.md`, "Rate limits through Vercel"). Both paths were checked on the live demo on 12 Oct 2026. With `PROXY_SECRET_REQUIRED=true`, as on the demo, `/api` refuses any request without the secret, so a copy of the frontend deployed by someone else cannot use the demo's API. The per-user upload limit and the global caps apply on every path.
- Security headers on the API (`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY` except for the rendered invoice view, a restrictive CSP on HTML) and on the frontend (`frontend/vercel.json`).
- Every error is `application/problem+json` without stack traces or internal messages.

## Secrets

All secrets (Django secret key, database URL, storage keys, OpenAI key, mailbox password) are read only from environment variables, once at start-up, by `backend/src/eingang/config.py`. They are never committed: `.env` files are git-ignored, and `backend/.env.example` contains only development defaults. In production they are set in the hosting providers' variable settings (see `docs/DEPLOY.md`). The storage keys and the OpenAI key are used only by the server; the browser talks only to the app's own origin.

## LLM data flow

LLM features are off unless `LLM_ENABLED=true`. When they are on, the worker sends data to the OpenAI API in three cases:

1. **Extraction** of a plain PDF (and of PDFs whose XML cannot be used): the PDF's text layer, cut to its first 12,000 characters.
2. **Comparison** of a hybrid PDF (when `COMPARE_HYBRID_PDF=true`): the PDF's visible text, cut the same way.
3. **Rule explanation:** a validation rule's ID, official message and source; no invoice data.

Invoice text inside a request is fenced and treated as data, not instructions. Requests are sent with `store=false`. Every call is first checked against the lifetime, monthly, daily-public and per-sandbox budgets; refused and failed calls are recorded in the ledger too. No call is made for scanned PDFs without a text layer, for structured XML invoices, or when opening a sandbox (the samples' results are precomputed).
