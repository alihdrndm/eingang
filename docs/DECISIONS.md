# Decisions and deviations

Dated record of every place the implementation differs from, or fills a gap in, `HANDOFF.md` (OP5, OP7). Newest last.

## 2026-10-09 — M0

### Temporal image pinned to `temporalio/temporal:1.9.1`
- **Spec:** pin the official `temporalio/temporal` image by version tag and record the tag.
- **Did:** `1.9.1`, the latest stable tag on Docker Hub when checked. `1.9.1`, `latest` and `release` shared one digest, pushed 2026-09-14, matching the `temporalio/cli` v1.9.1 GitHub release.
- **Why:** reproducible local and demo servers.

### Error codes for generic HTTP errors
- **Spec:** every non-2xx response is problem+json with a documented `code`. It names no codes for malformed JSON, a wrong method, a wrong `Accept` or `Content-Type` header, or `/readyz` failing.
- **Did:** added `MALFORMED_REQUEST` (400), `METHOD_NOT_ALLOWED` (405), `NOT_ACCEPTABLE` (406), `UNSUPPORTED_MEDIA_TYPE` (415) and `NOT_READY` (503). All are documented in `docs/ERRORS.md`. `/readyz`'s 503 carries the extension member `checks`, which says which dependency failed.
- **Why:** these responses must still be problem+json, and each code must be documented.

### `/readyz` checks run one after the other
- **Spec:** the database and Temporal are each checked with a 2-second timeout.
- **Did:** the checks run in sequence, each with its own 2-second limit. The worst case is therefore about 4 seconds. The database check runs on a separate thread and connection, so a hung connection attempt can't block the request beyond the timeout.

### Request id from the client is validated
- **Spec:** read `x-request-id` if present.
- **Did:** a client-supplied id is used only if it matches `[A-Za-z0-9._-]{1,128}`. Otherwise a new UUID v7 is generated.
- **Why:** the id is echoed in a header and written to every log line, so arbitrary input would allow header or log injection.

### Development API server
- **Spec:** `pnpm dev` runs the API with hot reload. The server's own access log is turned off.
- **Did:** in development the API runs on Django's `runserver` on port 8010, and its `django.server` access logger is silenced. The app's own request log line replaces it. Production uses gunicorn exactly as specified.
- **Why:** gunicorn does not run on Windows, and `uvicorn` is not an allowed dependency.

### Poe tasks put `src` on `PYTHONPATH`
- **Did:** the `lint-imports` and `worker` tasks set `PYTHONPATH=src`.
- **Why:** `backend/` is a virtual uv project (no build system), so `src/` is not installed into the environment. `manage.py` and pytest add it themselves.

### Worker with nothing registered
- **Did:** until the first activities (M2) and workflows (M4) exist, `python -m eingang.worker` logs that nothing is registered and exits with status 0.
- **Why:** the Temporal SDK refuses to start a worker with no workflows and no activities. `pnpm dev` still starts the worker process, so the wiring is already in place.

### Import-linter contracts arrive with their modules
- **Did:** `einvoice-is-pure` and `workflows-are-deterministic` are active now. `api-stays-light` and `one-llm-door`, plus the Django-app entries of the first two, are added once the modules they name exist (M3–M5). Until then import-linter rejects contracts that name missing packages.
- **Update (M3):** all four contracts are active, and every Django app is a root package in them. `api-stays-light` covers `*.api` and `*.serializers`; `sandbox.seed` joins it in M4, when the module exists. `one-llm-door` forbids `openai` everywhere except `llm.client`, which arrives in M5.

### Coverage thresholds per package arrive with the packages
- **Did:** M0 enforces the "everything else" threshold, 75% of lines with branch measurement, over `src/`. The stricter thresholds for `einvoice` (M1) and `invoices` (M4) are added once those packages contain code.

### Authentication classes empty until M3
- **Did:** DRF's default authentication and permission classes are empty in M0. The only endpoints are the public health checks and the schema. The session authentication subclass and the role permissions come with M3.

### LF line endings enforced with `.gitattributes`
- **Did:** `* text=auto eol=lf`.
- **Why:** this repository is developed on Windows with `core.autocrlf`. `.editorconfig` requires LF, and the generated files (`openapi.json`, `schema.d.ts`) must be byte-identical on every OS for the CI freshness checks.

### TypeScript pinned to 5.9
- **Spec:** latest stable release of every named package.
- **Did:** `typescript ~5.9.3`.
- **Why:** `typescript-eslint` 8.71 supports TypeScript below 6.1, and `openapi-typescript` 7.13 needs TypeScript 5. With 5.9, `pnpm peers check` reports no problems. Upgrade once both support the current TypeScript major.

### `vue-demi` build script disabled
- **Did:** `allowBuilds: { vue-demi: false }` in `pnpm-workspace.yaml`.
- **Why:** pnpm 12 refuses to install a dependency whose build script has not been approved. `vue-demi`, pulled in by `@tanstack/vue-query`, only needs that script to switch to Vue 2, and its default build already targets Vue 3.

### e2e job and `test:e2e` wait for the e2e stack (M8)
- **Did:** the frontend's `test:e2e` script is not added yet. The CI `e2e` job runs, but every step after checkout is guarded with `if: hashFiles('frontend/e2e/smoke.spec.ts') != ''`.
- **Why:** the e2e Compose profile, the Dockerfiles and the Playwright smoke test arrive in M8. GitHub does not allow `hashFiles()` in a job-level `if`, so the guard sits on the steps.

### Temporal container runs as root
- **Spec:** the `temporal` service mounts the named volume `temporal-data` at `/data` and uses `--db-filename /data/temporal.db`.
- **Did:** added `user: "0:0"` to the service.
- **Why:** the `temporalio/temporal:1.9.1` image runs as uid 1000 and does not create `/data`, so a new named volume is owned by root. The server then failed with "unable to open database file (14)". The container has no published ports beyond 7233 and 8233 and is used locally only. Railway volumes are root-owned as well, so the same applies at deploy time (M9).

### Entry points name their settings module
- **Spec:** nothing except `config.py` reads `os.environ`.
- **Did:** `manage.py`, `eingang/wsgi.py`, `eingang/worker.py` and `tools/export_openapi.py` call `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eingang.settings")`.
- **Why:** this is how Django finds its settings module. It writes a fixed value and reads no configuration, and every configuration value is still read only by `config.py`.

### Every error Django produces itself becomes problem+json
- **Did:** the request middleware replaces any error response (status 400 or higher) that is not already problem+json, for example a disallowed `Host` or an unknown route. Mapping: 404 `NOT_FOUND`, 403 `FORBIDDEN_ROLE`, 405 `METHOD_NOT_ALLOWED`, 5xx `INTERNAL`, anything else 400 `MALFORMED_REQUEST`. CSRF failures get their own documented code, `CSRF_FAILED`, through `CSRF_FAILURE_VIEW`. `APPEND_SLASH` is off, so no redirect is issued in place of a 404.
- **Why:** "every non-2xx response is problem+json", including responses that never reach a DRF view.

### `/docs` loads a pinned Swagger UI with its own CSP
- **Did:** Swagger UI `5.33.1` is loaded from jsdelivr, through drf-spectacular's split view, which serves the init script from the same URL instead of inline. The page's CSP allows scripts only from `'self'` and jsdelivr, and inline styles, which Swagger UI sets at runtime. Every other HTML response from the API gets `default-src 'none'; frame-ancestors 'none'` unless its view sets a policy.
- **Why:** "a restrictive Content-Security-Policy on any HTML the API serves". The browser loads the jsdelivr files only on the developer-facing `/docs` page, never in the app.

### `pnpm seed` and `pnpm db:reset` wait for their commands
- **Did:** both scripts call `manage.py seed_rules` and `manage.py seed_dev` (section "Sandbox" › "Local seed"). Those commands arrive in M3/M4. Until then `pnpm seed` fails with Django's "Unknown command", and `pnpm db:reset` checks that both commands exist before it drops anything, then stops with a message and changes nothing.

## 2026-10-09 — M1

### Worker libraries installed in development with `--extra worker`
- **Spec:** `saxonche`, `pdfplumber`, `pypdf` and `factur-x` are the optional dependency group `worker`.
- **Did:** `pypdf`, `pdfplumber` and `factur-x` are in `[project.optional-dependencies] worker` (`saxonche` joins in M2). Development, CI and the README use `uv sync --extra worker`, because the `einvoice` tests need these libraries. `uv run` does not remove installed extras.

### Type stubs for lxml and defusedxml
- **Did:** added the dev-only packages `lxml-stubs` and `types-defusedxml`.
- **Why:** mypy `strict` needs types for the parsing code. These packages add nothing at runtime. The alternative was per-module `ignore_missing_imports`, which would hide real typing errors in the most security-sensitive code.

### Any DOCTYPE in a PDF attachment is refused before factur-x runs
- **Spec:** detection uses `facturx.get_xml_from_pdf(data, check_xsd=False)`. Any XML with a DOCTYPE is rejected.
- **Did:** `einvoice.pdf.embedded_invoice_xml` lists the PDF's XML attachments with pypdf first. Each one passes two checks before factur-x is called: (1) its text, decoded as UTF-8, UTF-16 or UTF-32, must not mention `<!DOCTYPE`; (2) our safe parser (`xmlsafe.parse_xml`), which honours any declared encoding, UTF-7 for example, must not find a DOCTYPE. Either finding raises `UnsafeXmlError`. An attachment that is not well-formed for libxml2 is skipped, because factur-x cannot parse it either.
- **Why:** factur-x parses each attachment with lxml's default parser, which loads and processes an internal DTD. It does not fetch external entities by default in lxml 6. A byte scan alone missed a UTF-7 DOCTYPE in the review. The safe parse covers every encoding libxml2 understands, which is exactly the set factur-x's parser could read. A DOCTYPE mentioned inside a comment is refused as well. That is strict on purpose: no real invoice needs one.

### A PDF that cannot be read is `CorruptPdfError`
- **Spec:** "corrupt PDF" is a permanent failure in the worker (section "Temporal workflows", step 7). It does not name the error.
- **Did:** any exception from pypdf or pdfplumber while reading a PDF becomes `einvoice.errors.CorruptPdfError`, so the worker (M4) can treat it as permanent instead of retrying.

### `factur-x` logger capped at WARNING
- **Did:** the logging configuration sets the `factur-x` logger to WARNING.
- **Why:** at DEBUG and INFO that library logs the extracted invoice XML, and uploaded contents must never be logged.

### Tax total without a currency attribute
- **Spec:** BT-110 is the tax amount "whose `@currencyID` equals BT-5".
- **Did:** an amount without `@currencyID` is accepted as well. Every amount in the document currency normally carries the attribute, and the first matching amount wins. A second amount in another currency (BT-111) is never taken.

### factur-x is untyped
- **Did:** a mypy override with `ignore_missing_imports` for `facturx` only. The one call site, in `einvoice/pdf.py`, checks the returned value's type before using it.

### Ruff N802 is off in tests
- **Spec:** every rule, check and error code has a test whose name contains its ID. Ruff's `N` rules are selected.
- **Did:** `N802` (function names must be lowercase) is ignored under `tests/` only, so test names keep the IDs in their original case: `test_D1_…`, `test_C05_…`, `test_BR_DE_15_…`.

### Detection details the spec leaves open
- A hybrid PDF whose embedded XML is not well-formed is `hybrid_pdf_unsupported`, with the note "The embedded XML is not well-formed". It is treated like a plain PDF instead of being rejected, because its visible PDF is still a usable invoice. A DOCTYPE is still refused.
- `Detection` has two fields beyond the spec's list: `profile_version` (the XRechnung version the spec asks to record, for example `3.0`) and `ubl_credit_note` (the root is a UBL `CreditNote`, which the format label needs).
- Text extraction runs for every PDF, hybrid ones included, so `has_text_layer` and `page_count` are always set for PDFs.

### What `test_parse_corpus_correct` covers
- **Spec:** every corpus XML and hybrid PDF in a `correct` folder must parse.
- **Did:** the test parses all 172 files under `ZUGFeRDv2/correct/` and `XML-Rechnung/{UBL,CII,FX}/`, with no exceptions. `ZUGFeRDv1/correct/` holds legacy ZUGFeRD 1 files, which by design are detected but never parsed (`legacy_zugferd1`, treated like plain PDFs). A separate test asserts their kind.
- Confirmed by the owner (2026-10-09): ZUGFeRD 1 files are detected only, never parsed. ZUGFeRD 1.0 predates EN 16931, so it is not an e-invoice under the BMF letter of 15 October 2024. The specification already routes it to plain-PDF extraction with check C11.
- Two of those files, `MustangGnuaccountingBeispielRE-20140519_499.pdf` and `…20140522_501.pdf`, predate ZUGFeRD 1.0. Their root is in the draft namespace `urn:un:unece:uncefact:data:standard:CBFBUY:5`, so rule D2 classifies them as `hybrid_pdf_unsupported`. That is still the plain-PDF path, so nothing changes for the user.
- Corpus tests fail with "run `uv run poe fetch-corpus`" when the corpus is missing. They are never skipped. CI downloads the corpus before the tests.

### XPaths verified against the corpus (OP5)
- Every XPath in the mapping table was checked against the corpus. The 25 XML-Rechnung invoices that exist in both UBL and CII parse to identical canonical invoices, on every field except `notes`. A test keeps it that way.
- `notes` differ by design. UBL writes a note's subject code into the text (`#REG#Lieferant GmbH…`), while CII has a separate `ram:SubjectCode` element. The mapping takes "each `cbc:Note`" and "each `ram:Content`" as they are, so UBL notes keep the prefix.
- `not_validating_full_invoice_based_onTest_EeISI_300_CENfullmodel` is a deliberately inconsistent corpus file. It parses, but it is excluded from the cross-syntax comparison.
- Seller and buyer tax numbers: BT-32 exists only for the seller. Neither parser reads, and neither writer writes, a tax number for the buyer.

### Per-package coverage thresholds
- **Did:** `uv run poe test` runs pytest, which writes `backend/coverage.json` (git-ignored), and then `tools/check_coverage.py`. The script groups files by package under `src/` and enforces 90% of lines and 85% of branches for `einvoice`, 90% of lines for `invoices`, and 75% of lines for every other package. This replaces M0's single global threshold.

## 2026-10-09 — M2

### Where the M2 libraries live
- `saxonche` is in the `worker` extra, next to the other worker-only libraries (spec: "Only the worker image adds `saxonche`…").
- `python-stdnum` is a runtime dependency. The sample builder needs it now to make valid VAT IDs and IBANs, and checks C06/C07 need it in M4.
- `reportlab` and `pillow` are in the dev group, because only `tools/build_samples.py` uses them (spec: "sample builder only"). The samples are committed, so no production image needs them.

### The KoSIT scenarios' custom levels are applied
- **Spec:** steps 1–5 of "Validation"; our verdicts must agree with the official validator (M6 parity).
- **Did:** after the Schematron runs, `einvoice.validate` finds the first scenario in the vendored `scenarios.xml` whose `match` expression is true for the document, evaluating the XPath 2.0 with Saxon. It then applies that scenario's `customLevel` overrides, for example `BR-CL-23` → warning in XRechnung, or `CII-SR-452` → fatal. Documents that match no scenario keep each rule's own level.
- **Why:** the KoSIT validator does exactly this. Without the overrides, valid XRechnung files with, say, a UN/ECE unit code that is not on the list would be reported invalid, and parity would fail.

### SaxonC runs on one dedicated thread
- **Spec:** compile the Schematron once per process and cache it.
- **Did:** `einvoice.saxon` hands all Saxon work to a single executor thread. That thread owns the processor and the compiled stylesheets, in thread-local storage. Callers on any thread submit a job and wait for the result.
- **Why:** SaxonC's native runtime is bound to the thread that created the processor. Calling it from the worker's other threads, or releasing it on the main thread at exit, crashed the runtime in testing ("wrong IsolateThread"). One validation takes about 5–100 ms, so serialising costs little.
- `saxonche` ships no type information, so it has a mypy `ignore_missing_imports` override like factur-x.

### KoSIT test suite: all 86 instances validate
- `test_testsuite_instances_all_validate_as_valid` runs every instance of the vendored test suite (`standard/`, `extension/`, `technical-cases/`). All 86 are strictly `valid`, so nothing has to be listed.

### Corpus `fail` files that are not rejected
Of the 26 files in `ZUGFeRDv1/fail` and `ZUGFeRDv2/fail`, 7 are not detected as hybrid: legacy ZUGFeRD 1, a UBL inside a PDF, and an XML with a bad encoding attribute. 13 are `invalid`, among them the FNFE "BASIC" files, whose BT-24 is malformed (`urn:cen.eu:en16931:2017:compliant:factur-x.eu:1p0:basic`) and therefore `UNKNOWN`. The remaining six are kept by `NOT_REJECTED` in `tests/einvoice/test_validate_reference.py`:

| File | Our result | Why the corpus fails it, and why we don't |
|------|-----------|-------------------------------------------|
| `Avoir_FR_type380_MINIMUM.pdf`, `Avoir_FR_type381_MINIMUM.pdf`, `Avoir_FR_type381_BASICWL.pdf` | `not_applicable` | MINIMUM and BASIC WL are not EN 16931 invoices, so their rules are not applied (spec, "Validation" step 2). The detection still marks them "not an e-invoice" (check C11). |
| `noNetPriceValidation.xml` | `valid` | The net price (0.10) × quantity (400) does not equal the line amount (316.00). No EN 16931 rule ties these together, so the KoSIT validator accepts the file as well. Mustang applies its own extra check. |
| `wrongFilename.pdf` | `valid` | The embedded file is named `factur-y.xml`. factur-x still finds it, and the invoice XML is valid. The failure concerns the PDF container's file naming, which is out of scope (ASSUMED E1). |
| `ZUGFeRD_2_fully_compliant_complete.pdf` | `valid` | The embedded XML passes the XSD and EN 16931. The corpus gives no reason. The PDF was produced by iTextSharp 4.1 and is most likely not PDF/A-3, which Eingang does not check (ASSUMED E1). |

### The writer adds BT-23 (business process)
- **Spec:** `WriteOptions` carries what XRechnung requires beyond the model. Written XRechnung files must validate as `valid`.
- **Did:** `WriteOptions.business_process` (BT-23) defaults to `urn:fdc:peppol.eu:2017:poacc:billing:01:1.0`, the value in the corpus reference files. It is written as UBL `cbc:ProfileID` and CII `BusinessProcessSpecifiedDocumentContextParameter`. XRechnung 3.0 rejects invoices without it (`PEPPOL-EN16931-R001`). The parsers do not read it, because the canonical model has no field for it.

### Sample builder details
- The visible invoices are German, like real supplier invoices, with "Rechnungsnummer", "Brutto", "Zahlbetrag" and amounts as `1.190,00 EUR`. That is the vocabulary the M5 extraction prompt is written for.
- Hybrid samples are made with `facturx.generate_from_binary(..., check_xsd=True)`, so their XML also passes the Factur-X schema of their level.
- Builds are reproducible except for the three hybrid PDFs (S03, S04, S10): factur-x writes the current time into their XMP metadata. The samples are built once and committed, and `samples/manifest.json` holds the SHA-256 of the committed files, which a test checks.
- `samples/precomputed/<ID>.json` carries `text` for PDFs in the section 5 format, each page introduced by `--- page N ---`. It also carries `invoice: null` for the plain PDF and the scan, because their data comes from the LLM (M5) or from a person.
- `types-reportlab` is a dev-only stub package, so the builder type-checks under mypy strict.
- S02's paper is counted in cartons with unit code `XCT` (UN/ECE Rec 21). The plain `CT` is not on the list and triggers `BR-CL-23`, and the samples must be strictly `valid`.

### The visualisation is a static page, and its scripts are vendored
- **Spec:** the KoSIT two-step visualisation (UBL/CII → XR → HTML), shown in an `<iframe sandbox>` without scripts. The vendored files are "the XSLT and CSS".
- **Did:**
  - `fetch_rules.py` also vendors the release's `xsl/FileSaver-v2.0.5.js` and `xsl/xrechnung-viewer.js`, because `xrechnung-html.xsl` reads both with `unparsed-text()` and fails without them.
  - `einvoice.visualize` runs both official steps with the stylesheet parameter `lang=en`, since the UI is English.
  - It then post-processes the HTML: it removes every `<script>` element and every `on…` attribute. The page's tab switching is a script that can never run here, so it also removes the tab bar (`class="menue"`) and changes every `divHide` section to `divShow`, so all sections are visible.
  - The XR intermediate document is parsed with the safe parser before the second step.
- **Why:** without scripts, only the first tab (the overview) would be visible, and the lines and VAT breakdown would stay hidden. Removing the scripts as well as forbidding them (CSP, iframe sandbox) means the stored HTML is safe on its own. FileSaver.js is MIT-licensed; NOTICE reproduces its licence.

### Confirmed: the KoSIT validator skips Schematron after an XSD failure
- **Spec:** step 1, "the KoSIT validator does the same; confirm it in M2".
- **Confirmed:** in the validator source at tag v1.6.0, `SchematronValidationAction.isSkipped()` returns true when `isSchemaInvalid(results)`, that is, when schema validation failed. `einvoice.validate` does the same.

### XSD issues and the schema parser
- An XSD issue's `location` is the line number as text (`"12"`), the same field that holds an XPath for Schematron issues.
- The vendored XSD files are loaded with the same parser flags as `xmlsafe` (no entities, no DTD, no network). They are trusted files, but the XML-safety rule covers every parse.

## 2026-10-09 — M3

### Ruff DJ001 is off; migrations are lint-exempt for RUF012
- **Did:** `DJ001` (avoid `null=True` on text fields) is ignored project-wide. Generated migrations ignore `RUF012` and `E501`.
- **Why:** the "Database" table marks many text fields `(null)`, for example `vat_id`, `failure_reason` and `sender_email`, and the canonical model uses `None` for "absent". Storing NULL keeps "not set" distinct from an empty string. Partial unique constraints such as `unique (organization, vat_id) where not null` depend on it.

### Model details the "Database" table leaves open
- `Document.kind` and `format_label` are NULL until the worker has detected the file. The API process cannot detect: the api-stays-light contract keeps PDF libraries out of it.
- `ValidationReport.xsd_ok` is nullable: `not_applicable` reports validated nothing.
- Required user references (`Approval.decided_by`, `ExportBatch.created_by`) use `RESTRICT`, not `PROTECT`. A user with decisions or exports cannot be deleted on their own, but deleting a sandbox organisation still deletes everything in it. `PROTECT` blocked that cascade in testing.
- The LLM ledger's `organization` uses `DB_SET_NULL` (Django 6.1): the database itself has `ON DELETE SET NULL`, so spend stays on record however a sandbox is deleted (spec "The ledger and the cache are never deleted"). A test deletes an organisation in raw SQL.
- `Event` refuses updates and deletes through `save()`/`delete()`. Deleting the whole organisation or document still removes its events by cascade.

### Proxy hops for rate limits: `TRUSTED_PROXY_HOPS`
- **Spec:** set DRF's `NUM_PROXIES` to the measured number of proxy hops, and record it.
- **Did:** a new setting `TRUSTED_PROXY_HOPS` (default 0, in `.env.example`) feeds `NUM_PROXIES`. The hop count of Vercel → Railway can only be measured on the deployed stack, so M9 measures it and records the number here and in `docs/DEPLOY.md`. A test proves that a client-supplied `X-Forwarded-For` does not change the throttle key.
- **Measured (11 Oct 2026): 2.** Railway's edge appends its own per-request proxy address, so a direct request arrives as `client, railway-proxy`; 2 keys on the client and ignores a client's fake entries (verified with plain and spoofed bursts of 150). Through Vercel no value from 1 to 4 limited anything, because the counted entry differs per request on that path. That path is handled by the next entry.

### Rate limits through Vercel: a middleware and a shared secret
- **Did:** `frontend/middleware.ts`, a Vercel Routing Middleware for `/api/*`, sends the visitor's address (`ipAddress()` from `@vercel/functions`) in `X-Eingang-Client-IP` with `PROXY_SHARED_SECRET` in `X-Eingang-Proxy-Secret`, after dropping both headers from the visitor's request. The per-IP throttles use that address only when the secret matches (constant-time comparison, value must parse as an IP); otherwise they fall back to `NUM_PROXIES`. The per-user upload limit is unchanged.
- **Why not the alternatives:** refusing direct requests to the Railway domain would also block `/healthz`, `/readyz` and `/docs`, which are opened there. Vercel's own `X-Forwarded-For` cannot be used: the address that reaches the API changes per request, and a direct request can send any value.
- **Cost:** one middleware invocation per `/api` request, inside the Vercel Hobby allowance at demo traffic.
- **Checked on the live demo (12 Oct 2026):** 150 parallel requests through Vercel gave 120 × `200` and 30 × `429`; the same burst with a different fake `X-Forwarded-For`, `X-Eingang-Client-IP` and a guessed secret on every request gave 120 × `200` and 30 × `429` as well. Straight to the Railway domain with the secret, 150 different forwarded addresses were all `200` and one forwarded address was limited after 119.

### Only the own web app may call /api: `PROXY_SECRET_REQUIRED`
- **Why:** `frontend/vercel.json` names the demo's API host, so anyone could deploy the frontend unchanged and send traffic to that API from changing Vercel addresses, past the per-IP limits, on the owner's Railway bill.
- **Did:** with `PROXY_SECRET_REQUIRED=true`, a middleware answers `/api/*` with `403 PROXY_REQUIRED` unless the request carries `PROXY_SHARED_SECRET`, which only the own Vercel middleware sends. `/healthz`, `/readyz` and `/docs` stay open for Railway's health check and readers. Default `false`, so local development and self-hosting need nothing.
- **Trade-off:** the API can no longer be called directly on the Railway host; measuring `TRUSTED_PROXY_HOPS` again needs the switch off for the duration.

### Login is CSRF-protected too
- **Did:** `POST /auth/login` checks the CSRF token like every other unsafe request, although the visitor has no session yet. This prevents login CSRF. The SPA fetches the token from `GET /auth/csrf` first, as the spec describes. CSRF failures answer `403 CSRF_FAILED`.

### Sandbox limits and session
- The 50-per-day limit counts the sandbox organisations created in the last 24 hours, in the database. A cache counter would reset with every deploy or restart. The 5-per-hour-per-IP limit is a DRF throttle. Both answer `429 SANDBOX_LIMIT`; the per-IP one adds `Retry-After`.
- The sandbox session's expiry is set as a relative age (`expires_at − now`), which ends at the same moment as `expires_at`. Django's session clock is the real clock, while sandbox expiry checks use the injected clock (spec "Testing standards"), so a relative age keeps both consistent in tests.
- `POST /sandbox` is CSRF-protected like login.

### `allowed_actions` details
- Roles per action follow the endpoint table: edit, resolve, mark reviewed, send back, reopen and retry are admin/accountant; approve and reject are admin/approver; delete is admin.
- No document action is closed to sandbox visitors. The spec restricts only members, organisation settings other than the name, and deleting the organisation. `SANDBOX_RESTRICTED` therefore never appears in `allowed_actions`; it stays in the reason order for completeness.
- `FOUR_EYES` applies to approve and reject when the organisation has four-eyes on and the user prepared the document: its `reviewed_by`, or the actor of its `document.received`, `invoice.fields_edited`, `check.resolved` or `review.completed` events (see "Four-eyes counts everyone who prepared the invoice" below). `BLOCKING_CHECKS` applies to "mark reviewed" and counts unresolved `block` checks only.

### Upload details
- The size-limited upload reader is installed by `invoices.middleware.UploadLimitMiddleware` for every multipart POST. DRF's CSRF check reads `request.POST`, which parses the body before the view runs, so a view cannot choose the reader. The middleware also refuses a body larger than ten maximum-size files from `Content-Length` alone, and a single file over the limit stops reading at the first byte past it. Both answer `413 FILE_TOO_LARGE`.
- A request is all or nothing: if any file is of an unsupported type, nothing is stored (`415 UNSUPPORTED_FILE`). The SPA sends one file per request anyway.
- Duplicates (same SHA-256 as a non-deleted document of the organisation, or repeated within the request) are reported in `duplicates` with the existing document's id, and a `document.duplicate_upload` event is recorded on that document.
- The sandbox's upload count includes deleted uploads, so deleting does not reset the per-sandbox limit.
- If Temporal does not accept the start, the documents stay stored with status `received` and a `workflow_id`, and the response is `503 TEMPORAL_UNAVAILABLE`. The daily maintenance starts them later (M4).

### DRF's `?format=` override is off
- **Spec:** `GET /documents?format=` filters by format label.
- **Did:** `URL_FORMAT_OVERRIDE` is set to `None`. DRF otherwise reads `?format=` as a renderer name and answers 404 for "Plain PDF".

### Document detail, files and delete
- Each check's `resolve` uses `FORBIDDEN_ROLE` (C15 is admin-only), `CHECK_NOT_RESOLVABLE` (information-only or already resolved) and `INVALID_TRANSITION` (the document does not need review). These are the same codes `POST /checks/{id}/resolve` answers (M4).
- `/file`, `/xml` and `/text` are sent as attachments with `Content-Security-Policy: sandbox; default-src 'none'`. `/visualization` gets the CSP of section 4 and `X-Frame-Options: SAMEORIGIN`. A representation the worker has not stored answers `404 NOT_AVAILABLE`.
- Delete is a soft delete (`deleted_at`) with a `document.deleted` event. Exported documents answer `409 ALREADY_EXPORTED`. Documents still `received` or `processing` answer `409 INVALID_TRANSITION`, because `allowed_actions` offers no delete in those statuses. Signalling the running workflow (`deleted`) arrives with the workflows in M4.

### Order of checks on requests that are wrong in several ways
- DRF checks sign-in and role before the HTTP method, so an anonymous request with a wrong method gets 401, not 405. Signed in, it gets `405 METHOD_NOT_ALLOWED`.
- The CSRF check reads the request body, so a JSON body sent to the multipart-only `POST /documents` gets `415 UNSUPPORTED_MEDIA_TYPE` before the role check. With multipart, as the SPA always sends it, a viewer or approver gets `403 FORBIDDEN_ROLE`.
- `tests/test_error_codes.py` pins this order, and checks that every code the code base uses is documented in `docs/ERRORS.md`.

### M3 scope: which endpoints land now, which in M4 and M6
- **Spec:** the M3 row names "documents/suppliers/organisation/members endpoints". The HTTP API table does not assign routes to milestones.
- **Did (M3):** auth (csrf, login, logout, me), `POST /sandbox`, `POST`/`GET /documents`, `GET`/`DELETE /documents/{id}`, `/file`, `/xml`, `/text`, `/visualization`, suppliers list and detail, organisation `GET`/`PATCH`, members `GET`/`POST`/`PATCH`.
- **Moved to M4:**
  - `PATCH /documents/{id}/invoice`, `POST /checks/{id}/resolve`, `mark-reviewed`, `decision`, `send-back`, `reopen` and `retry`. They need the section 9 status machine, checks re-run on edit, and workflow signals (`retry` is signal-with-start), all of which are M4 deliverables.
  - Exports, which need the `exported` transition and its signal.
  - `/stats`, which needs C08 and the LLM ledger figures.
  - `/rules/{id}`, which needs `seed_rules`.
- **Moved to M6:** `/accuracy`, by its row.
- **M4 owes:** API tests for those endpoints and their codes `BLOCKING_CHECKS`, `FOUR_EYES`, `CHECK_NOT_RESOLVABLE`, `INVALID_TRANSITION` and `NOTHING_TO_EXPORT`. In M3 these appear only as `allowed_actions` and resolve reason codes, which are tested.

### `processing_step` is nullable
- A document has no processing step until its workflow starts (`received`), so `processing_step` is NULL until then, and the API omits it.

### Member updates
- `PATCH /members/{id}` accepts `email`, `name` and `role` (the spec's body), with the email lower-cased and unique across all organisations. It also accepts `is_active`: there is no delete endpoint, so deactivating is how an admin removes someone, and an inactive user cannot sign in. An admin cannot deactivate themselves or change their own role (422).
- `GET /members` is paginated like every other list (25 per page).

## 2026-10-09 — M4

### One test database per test run
- **Spec:** database tests run against a separate test database `eingang_test`.
- **Did:** the test database is `eingang_test_<process id>`. pytest-django creates and drops it per run, and it is never the development database.
- **Why:** this project runs several checks in parallel, for example a reviewer's `pnpm verify` next to a build. Two runs sharing one test database dropped it under each other.

### Business checks: where the spec is silent
- **C01:** an empty VAT breakdown is "not given" and is not compared with the tax total. A missing `prepaid_amount` skips the payable comparison ("Fields that are None are skipped"). A line without a net amount skips the line sum.
- **C02, C03:** `details.document_id` links the earliest matching earlier document. C03 needs a different normalised number; the window `duplicate_window_days` is inclusive.
- **C04:** needs a supplier, so a scan without data gets no C04 yet (sandbox table, S09).
- **C05:** an IBAN with no history row counts as not trusted.
- **C11:** the message names the case (plain PDF, scan, legacy ZUGFeRD 1, unsupported hybrid PDF, or the profile) and links the European Commission page. The link is also in `details.source_url`.
- **C14:** names are compared with `rapidfuzz.fuzz.token_set_ratio` after rapidfuzz's default processing (lower-case, punctuation removed), so an upper-cased buyer name is not "someone else". VAT IDs are compared upper-cased without spaces. Nothing fires when the organisation has no VAT ID and the invoice has a buyer VAT ID.
- **Messages:** where the spec gives no exact text (C01–C04, C06–C09, C13–C16), the messages are written in plain English. C04's and C15's follow the design export's check cards.
- **Supplier IBAN history:** an IBAN's first sighting is always its earliest received invoice, even when invoices are processed out of order, so trust ("on the supplier's first invoice") never depends on processing order.

### Exports: where the spec is silent
- **Explicit `document_ids`:** ids that are not approved, are deleted, belong to another organisation or don't exist are left out silently. `409 NOTHING_TO_EXPORT` comes only when nothing is left, and an empty list counts as nothing.
- **Credit notes:** in the invoices CSV the amounts are negated; in the lines CSV only `Netto` is. Quantity, unit price and rate keep their stored sign, and the JSON report keeps the canonical signs. Zero is written `0,00`, never `-0,00`.
- **Numbers:** money has two places, rounded half up; quantity, unit price and rate are written exactly as stored. No thousands separator.
- **`Freigegeben am`:** the date of the latest approving decision, in Europe/Berlin. `Validierung` is the raw report status.
- **Rows:** oldest received first. The formula guard applies to every text cell, not to numbers or dates.
- **Concurrency:** the approved rows are locked before building, so two exports at once can't both include a document; the second gets `NOTHING_TO_EXPORT`. The file is written before the batch row, so a later failure leaves an orphaned file and no batch.

### Review endpoints
- **Editable fields:** every scalar canonical field (number, type code, dates, currency, references, party fields, payment fields, totals). `notes` and the VAT breakdown are not editable; lines are replaced as a whole list and renumbered from 1. An empty string clears a field.
- **Audit trail:** one `invoice.fields_edited` event per changed field. A replaced line list is logged as field `lines` with the old and new line counts and, since 2026-10-28, both full line lists (see "Edit events keep the original values" below). The IBAN is logged with `"change": "changed"` and, since 2026-10-28, masked old and new values only.
- **Re-running checks after an edit:** C09 and C12 come from processing (the PDF comparison and the extraction refusal), so the re-run rebuilds that context from the stored C09/C12 checks instead of dropping them.
- **Resolving C05** confirms the supplier's IBAN (`confirmed_by`, `confirmed_at`, `confirmation_note`) unless it is already confirmed.
- **Retry:** `failed → processing` is committed first, then the workflow gets signal-with-start `retry`; the workflow re-reads the status, so the commit must come first. If Temporal can't be reached, a system transition moves the document back to `failed` with its old reason and the answer is `503 TEMPORAL_UNAVAILABLE`.
- **Delete** now signals `deleted` after the commit, like every other action.
- **A document that no longer exists** (its sandbox expired while a workflow ran) is a permanent error for the processing activities; `read_status` reports it as deleted, so the workflow ends instead of failing.

### Stats and rule explanations
- `blocked`, `overdue` and `awaiting_my_approval` count the same set as `by_status`: the organisation's non-deleted documents. Any `reminder.sent` event marks an `awaiting_approval` document overdue, including one from an earlier approval round.
- `seed_rules` keeps an explanation that came from the LLM (a stored rule is never re-explained) and checks the whole file before writing anything.

### Maintenance
- The maintenance activities live in `eingang/maintenance_activities.py` (they span several apps), and `ensure_schedules` in the `eingang` app, which is now in `INSTALLED_APPS` (it has no models).
- One failing step doesn't stop the others; the summary lists the failed steps.
- The resync step skips `exported` (final) and `received` (handled by `start_unstarted_documents`). It describes each workflow before querying it, so only running workflows are compared; open documents without a running workflow (for example after the 180-day wait) are counted and logged as abandoned.
- A sandbox is expired when `expires_at <= now`. Its running workflows get `deleted` first; then its files (originals, derived files, exports) and the organisation (cascade) are deleted.
- The mailbox schedule exists only when `MAILBOX_ENABLED` is true and the worker registers the mailbox workflow; otherwise `ensure_schedules` removes it. Schedules are tested with a recording fake client, because the time-skipping test server doesn't implement schedules.

### Sandbox seed
- The seed reads detection, validation, invoice and text from `samples/precomputed/`, then runs supplier matching, the checks and the status rule with the workflow's own functions (`invoices/persist.py` is shared with the activities). Hybrid PDFs get the timeline note "Visible PDF not compared", as a real run without the LLM writes; S04 is a hybrid PDF too, so it gets the note as well.
- History dates: rows are created at seed time, then each document's events are dated one second apart from its `received_at`, and the two sample decisions (S11, S12) a day after arrival. Only the seed rewrites dates, and only of rows it just created.
- `seed_dev` resets the local organisation by deleting it with its files (the same code that deletes expired sandboxes) and creating it again. Its name is "Holzwerk Brandt GmbH (local)" with the buyer's VAT ID, so C14 doesn't fire.
- Every test writes stored files into a temporary folder (an autouse fixture), never into the development storage.

### Mailbox intake
- `fetch_mail` uses IMAP UID commands, reads at most 20 messages per poll with `BODY.PEEK[]`, and marks a message seen only after its attachments are stored in one transaction. The activity timeout is 120 s; each socket operation times out after 30 s.
- Attachments get the upload rules (size, type by content, DOCTYPE refused); a message larger than twice the upload limit plus 256 KiB is marked seen without downloading. Duplicates are skipped and only logged. A crash between storing and marking seen is repaired by the maintenance, which starts documents left in `received`.
- A refused login, a disabled mailbox or an unknown organisation is a permanent error; other IMAP errors are retried. Logs contain counts and document IDs only.
- The mailbox workflow is always registered in the worker; `MAILBOX_ENABLED` alone decides whether the schedule exists.

### Workflow signals and review fixes
- **Signals carry no payload:** `temporal_client.signal(document_id, workflow_id, name)` sends only the name, and `decided` carries no decision. Every signal only wakes the loop, which re-reads the status (the decision is in the database), so a payload could only disagree with the database. The workflow ID is passed in so seeded documents (empty ID) are skipped without a query.
- **Retry by status:** a waiting workflow that reads `processing` processes again, with or without the `retry` signal. Only a retry moves a waiting document back to `processing`, so a lost or raced signal is recovered by the next wake (at the latest the daily `sync`).
- **Reminders** fall every `reminder_after_days` from when approval began; a wake in between doesn't move them.
- **`COMPARE_HYBRID_PDF=false`:** the comparison activity answers "not compared" without a call, and the workflow skips it silently, with no timeline note. The note is for a comparison that was tried and failed.
- **Edits store IBAN, BIC and VAT IDs upper-case without spaces**, as parsing does, so an IBAN typed with spaces matches the supplier's history.
- **ZIP entry names:** originals are stored as `originals/<Eingang-ID>-<name>`, with the name reduced to `[A-Za-z0-9._-]` (other characters become `_`). An uploaded name is untrusted and must be safe in every unzip tool; the Eingang-ID keeps it unique and traceable.

## 2026-10-11 — M5

### The LLM client
- **One lock for budgeted calls:** the budget check, the call and its ledger row run while a Postgres advisory lock is held, so two workers can't both pass the same last check. Calls are rare, so serialising them costs little.
- **Ledger rows survive errors:** the call's transaction commits its ledger row first; the refusal or error is raised afterwards.
- **Errors:** a refused request (HTTP 400) is permanent; timeouts, rate limits and server errors are recorded and left to Temporal's two attempts; an incomplete answer or one that doesn't parse is paid for, recorded with its tokens and refused as permanent. The client reads the raw response (`with_raw_response`) so status and usage are known before parsing, because the SDK raises while parsing JSON cut off at the token limit.
- **Per-sandbox counter:** it is re-read from the database inside the budget lock, so a loaded organisation that predates earlier calls (an activity explaining several rules) can't pass the check again.
- **Per-sandbox cap:** every call that reaches the API counts (including failed ones); cache hits don't, because they cost nothing. Calls for a sandbox are marked `is_public` in the ledger, so a deleted sandbox's spend still counts against today's public budget.
- **Ledger time** comes from the injected clock, so the monthly and daily windows are testable.
- **Cache key:** model, prompt version, the exact two input messages (instructions, fenced data) and the output schema. `store=false` is sent, so OpenAI keeps no copy of the conversation.
- **Fences:** a closing tag inside untrusted text is broken up (`</ document>`), so the text can't end its fence.
- **NUL in answers:** every string of a parsed answer is cleaned of NUL before it is cached or returned, because PostgreSQL stores no NUL in text or jsonb (the evaluation run met one in quoted evidence). The cache write runs in a savepoint, so a failed write keeps the call's ledger row; a lost cache entry only costs a repeat call.
- **Prompt version on results:** an LLM-extracted invoice stores `prompt_version` (new column); rule explanations already did.
- **Model: `gpt-5-nano`, reasoning effort `minimal`**, confirmed by the owner on 11 Oct 2026. It is the cheapest of the 133 models the project key lists (`models.list`) on the official pricing page (developers.openai.com/api/docs/pricing, read 11 Oct 2026): $0.05 input, $0.005 cached input, $0.40 output per million tokens. Next were `gpt-4.1-nano` ($0.10 / $0.025 / $0.40) and `gpt-4o-mini` ($0.15 / $0.075 / $0.60). It supports Structured Outputs (strict JSON schema) on the Responses API, and the smoke call returned complete, schema-valid answers for all three prompts. Estimate shown to the owner before confirming: the M6 evaluation run (104 documents) at most about $0.11 by the budget formula, about $0.04 at the smoke call's real usage; `EVAL_BUDGET_USD` caps it at $0.75. The model and prices are settings (`backend/.env.example`), not defaults in code.

### Extraction grading (section 5)
- A value the model left null gets no confidence entry, so C13 doesn't fire for it: a value that isn't printed isn't a doubtful value.
- A value that can't be stored as read (unparseable, more than two decimals, longer than its column) is stored as null but keeps `low` and its evidence, so C13 asks for it. A storable value that fails its validator (IBAN checksum, VAT ID, date range, currency) is kept as read and `low`, so C06/C07 can name the problem.
- Evidence must appear verbatim (case-sensitive, whitespace collapsed); the 80-character limit is not enforced, because long values such as payment terms couldn't meet it.
- Numbers: day-first dates (month-first only when day-first is impossible), German and English amount formats, a single mark followed by exactly three digits means thousands. ISO 4217 is a fixed list of active codes.
- Rule explanations longer than 300/200 characters are cut at a word break with "…", because strict Structured Outputs can't enforce a maximum length.
- The comparison counts a PDF value only when its evidence is in the visible text; a value the model didn't find is not a difference.
- **S08 is seeded without C13.** The recorded `gpt-5-nano` answer graded every field `high` (each value printed, with verbatim evidence), so the sample shows C04 and C11 and `needs_review`. The M5 acceptance line expects "C04, C11, C13s"; the sandbox table's "C13 for each low-confidence field" allows none. The sample keeps the real answer rather than a doctored one; the C13 path is tested with a synthetic answer (`tests/sandbox/test_seed.py`) and through the injection test.

## 2026-10-10 — M6 to M8

### Evaluation (M6)
- **Layout:** `evals/` is a package at the repository root, run from `backend/` as `python -m evals.<script>` with `PYTHONPATH=..` (poe tasks `eval-*`), so it uses the backend's environment and `.env`. The `latest.json` models live in `eingang/accuracy.py`, because application code never imports from `evals/`; `evals/schema.py` imports them.
- **`/accuracy`** is described in OpenAPI by DRF serializers (drf-spectacular's Pydantic support produces OpenAPI 3.0 errors); a test keeps them in step with the models. A malformed `latest.json` is a build error (500).
- **Dataset:** identical PDFs count once; MINIMUM and BASIC WL hybrids are kept (HANDOFF doesn't exclude them; their truth has fewer fields). The language guess is keyword counts and only describes the dataset.
- **Regex baseline:** labels are tried in priority order. They were extended after looking at the corpus layout (recorded in `docs/EVALS.md`): the first version scored 3.8%, the extended one 21.2%. That tuning favours the baseline.
- **Parity:** KoSIT's report prints each rule's original level and applies the scenario's `customLevel` entries only when assessing; the comparison applies the same custom levels to decide which rules were fatal. Schema and well-formedness errors are compared as Eingang's `XSD`. The KoSIT validator v1.6.0 and the full configuration release are downloaded into `data/kosit/` and pinned by SHA-256.
- **Accuracy page legend differs from the design export.** The export defines "Invented" as a value where none is printed and "Missing" as nothing returned where a value is printed. Both are measured against the embedded XML, and the M6 run showed printed IBANs that the XML lacks, so the page says "read where the XML has none; it may still be printed" and "nothing read where the XML has a value".
- **Zero-denominator rates are left out of `latest.json`** (HANDOFF's shape lists all three keys per field): a rate with nothing to count is absent, not 0, and the page shows "—". Parity has a third exclusion kind, `unsupported` (a file that is not an invoice Eingang accepts), besides HANDOFF's two.
- **LLM evaluation runs** are counted per prompt version in `evals/data/llm-runs.json` (committed); a third run needs `--third-run-approved`. A document whose call fails counts as abstaining on every field; a budget refusal ends the run, and the report shows how many documents were scored.

### Frontend (M7)
- **API client:** the generated paths already start with `/api/v1`, so the client's base URL is the page's origin. The CSRF cookie is read on every unsafe request (Django rotates it at sign-in); `GET /auth/csrf` is called only when no cookie exists, or once to retry a `CSRF_FAILED`. The `Problem` type is written by hand, because the OpenAPI schema doesn't describe problem+json bodies.
- **Session:** one 401 hook lives in the client and acts only on `/app/*`; the route guard handles the first `/auth/me`. An expired sandbox's message is a one-time notice read by the homepage. `?redirect=` after sign-in is honoured only for `/app/` paths. The query cache is cleared on sign-out.
- **Uploads** use XMLHttpRequest (the only browser API that reports upload progress) with the same CSRF and problem handling; files the browser can already refuse (type, more than 4 MB, more than 10 at once) are refused before sending, each with its own row.
- **Inbox state lives in the URL** (`tab`, `q`, `supplier`, `format`, `ordering`, `page`), so back/forward and links work; rows pass the list query as `?from=` so the review's `j`/`k` walk the same list (its current page).
- **Disabled actions** keep the real `disabled` attribute (as HANDOFF says) with the reason as visible text linked by `aria-describedby`.
- **PDF.js** loads on demand (its own chunk); the XML visualisation is shown in `<iframe sandbox="">` from the API's URL, so its Content-Security-Policy still applies.
- **End-to-end tests** live in `frontend/e2e/` with their own TypeScript config (Node types stay out of the app); Vitest runs only `src/**/*.test.ts`.
- **HTTP in frontend tests is mocked with a small hand-written `fetch` stub** (`src/testing/http.ts`), not msw. Every request goes through one `fetch`, so a stub that records method, path, headers and body (and answers with typed problem bodies) covers what the tests need without a service worker or another dependency. The XHR upload has its own fake request.
- **Validation line by profile:** only XRechnung 3 invoices say "Checked with the official XRechnung rules"; XRechnung 1.x/2.x say "XRechnung 2.x: checked against EN 16931 rules only" and EN 16931, BASIC and EXTENDED say "Checked against EN 16931 rules only" (E3, E5).
- **Evidence** shows while the eye button is hovered or focused and stays open after a click.

### Containers and the e2e stack (M8)
- **Images:** two-stage builds on `python:3.13-slim` with uv 0.12.24 pinned (`ghcr.io/astral-sh/uv:0.12.24`), running as a non-root user. The API image installs without the `worker` extra and carries `samples/` and `evals/latest.json` at the same relative paths as the repository; the worker image adds the extra. The frontend image (`node:24-slim`, pnpm via Corepack) is used only by the e2e stack and keeps dev dependencies, because `vite.config.ts` imports the Vitest config.
- **Compose:** `db` and `temporal` have no profile, so they belong to both the development project and the separate `eingang-e2e` project; `pnpm dev` is unchanged. The e2e services run with production settings (`DJANGO_DEBUG=false`, a dummy secret key), so cookies are `Secure`; browsers accept them on `localhost`. Only `web` publishes a port (3110), so the browser talks to one origin. Health checks live in Compose (`/healthz` for the API), not in the Dockerfiles.
- **Smoke test** clicks the format's label card (as a person would) and counts the inbox's table rows.
- **Supplier IBAN history** names the document of its first sighting (`first_seen_document_id`), so the UI links it directly instead of matching invoices by time.
- **Images install dependencies only** (`uv sync --frozen --no-dev --no-install-project`, plus `--extra worker` for the worker) and run the source from `/app/backend/src` on `PYTHONPATH`. The result is the same as HANDOFF's `uv sync --frozen --no-dev`, without building the project itself as a package.

### Definition-of-done checks
- The marker search of the Definition of done matches only the vendored KoSIT stylesheets in `backend/vendor/`, where the ISO 4217 "no currency" code appears inside the official currency list. Third-party files are kept byte for byte as released, so they are not edited.

## 2026-10-11 — M9

### Deployment
- **Railway configuration lives in the dashboard, not in `railway.api.json` / `railway.worker.json`.** Railway has deprecated config as code: new services can't opt in, and existing `railway.json` files stop working after 2026-12-01 (docs.railway.com, "Config as Code"). Its replacement (`.railway/railway.ts`) needs another npm package and a CLI apply step. `docs/DEPLOY.md` lists every setting to enter per service instead (Dockerfile path via `RAILWAY_DOCKERFILE_PATH`, pre-deploy command, health check, restart policy).
- **No BuildKit cache mounts in the backend Dockerfiles.** Railway's builder rejects `RUN --mount=type=cache` without an `id` naming the Railway service (`s/<service-id>-…`), which would tie the repository to one deployment. The mount only sped up repeated local builds; the images are the same without it. The frontend Dockerfile (e2e only) keeps its pnpm store cache.
- **`PGSSLMODE=require`** is set for api and worker in production, so the database connection is encrypted whatever the URL says (the settings ignore the URL's query string).
- **The Temporal start command names the executable:** `temporal server start-dev …` instead of HANDOFF's `server start-dev …`. Railway's custom start command replaces the image's entry point (`temporal`), and without it the deploy failed with "The executable `server` could not be found".
- **The pre-deploy command is wrapped in `sh -c "…"`.** Railway runs it without a shell, so HANDOFF's bare `migrate && seed_rules && ensure_schedules` chain would pass `&&` as an argument.
- **`ALLOWED_HOSTS` on the API includes `healthcheck.railway.app`.** Railway's deploy healthcheck sends that `Host` header; without it Django answers `400` and the deploy never goes live.
- **The four secrets are Railway shared variables**, referenced from `api` and `worker` as `${{shared.NAME}}`, so each is entered once.
- **Region is set after the first deployment.** Railway shows the region selector only for a deployed service, so each service deploys once in the default region and then moves to `europe-west4`.
- **The Railway usage email alert is at USD 5, not USD 4.** It was set to 5 during the deploy, as the owner's setting; the USD 10 hard limit still caps the bill, and `docs/DEPLOY.md` keeps 4 as the suggestion for a new deployment.

### README
- **The README has more sections than the planned outline.** Spec: pitch, demo link, three-image strip, then fixed sections. Did: the same sections in the same order, plus a hero image, a four-image feature grid, a contents list, a formats table, the invoice lifecycle diagram, the architecture image (the Mermaid chart is kept in a collapsible block), "Tests and quality" and "Security and privacy". Why: the README is the project's public page; it now names the formats and standards people search for (XRechnung, ZUGFeRD, Factur-X, EN 16931, KoSIT) and shows the evaluation's limits next to its numbers. The only badge is the CI badge.



## 2026-10-28 — Detection and business-check fixes

### Profile from BT-24
- **Any CIUS or extension of EN 16931 counts as EN 16931.** A BT-24 that starts with `urn:cen.eu:en16931:2017#compliant#` or `#conformant#` and matches no XRechnung or Factur-X/ZUGFeRD profile (for example Peppol self-billing, `…#compliant#urn:fdc:peppol.eu:2017:poacc:selfbilling:3.0`) is profile `EN 16931`, so it is validated against the EN 16931 rules and C11 doesn't fire. Before, only the bare identifier and Peppol BIS Billing were recognised. Malformed identifiers (`…:2017:compliant:…`, or nothing after the second `#`) stay `UNKNOWN`.

### UBL dates with a time zone
- **A UBL date's time zone is dropped and the day kept as written.** `xsd:date` allows `2026-10-01Z` or `2026-10-01+02:00`; the invoice's day is what the supplier printed, so converting it to UTC would move it. Anything else after the day (a time, a malformed offset) is still a parse error. CII dates are format 102 (`YYYYMMDD`), which has no time zone, so the CII parser is unchanged.

### Which type codes are credit notes
- **A credit note is any BT-3 code in `einvoice.model.CREDIT_NOTE_TYPE_CODES`: 81, 83, 261, 262, 296, 308, 381, 396, 420, 458, 532.** These are the UNTDID 1001 credit-note codes of the EN 16931 code list, the set Peppol BIS Billing 3.0 allows in a UBL `CreditNote` (rule PEPPOL-EN16931-P0101). Before, only 381 counted. The one set decides the " · credit note" format label, negative amounts in the CSV exports, whether `einvoice.write` writes a UBL `CreditNote`, and the business checks. XRechnung 3 itself allows only 381 among them; other codes still pass through as they were sent. The frontend still marks only 381 as a credit note (`frontend/src/features/review/fields.ts`).

### Credit notes in C03 and C08
- **C03 compares only documents of the same kind:** a credit note only with earlier credit notes, an invoice (any other or no type code) only with earlier invoices. A credit note that cancels an invoice usually repeats its gross total and lands within the window, which is not a duplicate.
- **C08 doesn't fire for a credit note, or when the amount payable is zero or less:** nothing is owed, so nothing is overdue. A missing amount payable still lets C08 fire, as before.

### C01: net total and VAT breakdown
- **C01 also compares** the net total with line total − allowance total + charge total (BR-CO-13), the sum of the breakdown's taxable amounts with the net total, each within the existing one-cent tolerance, and each breakdown row's tax amount with taxable amount × rate / 100 rounded half-up to the cent (BR-CO-17) within one currency unit, the tolerance of the official Schematron rule, so C01 never flags a row that the validator accepts. A missing allowance or charge total counts as zero, and both are taken as absolute amounts because AI extraction keeps a printed minus sign ("Rabatt -50,00"), because EN 16931 leaves BT-107/BT-108 out when there are none; the comparison needs the line total and the net total. The taxable sum is skipped when any row has no taxable amount (like the tax sum), and a row without a rate, taxable or tax amount is skipped. The two breakdown comparisons name `tax_breakdown` in `details.fields`; each field is listed once. The severity is unchanged (block for LLM extraction, warn otherwise).

### XML values that don't fit their column
- **An XML value longer than its column is stored as None**, on the invoice (including `spec_id`) and on its lines, as the LLM path already does (`invoices.extraction.fits_column`). Before, a three-letter country code or an overlong BIC failed the insert, the activity was retried and the document ended "failed after several attempts". The XML itself is kept and shown, so the value can still be read there. Numbers are not checked: the parser rounds them to the columns' decimal places, and an amount too large for its column (more than 16 digits before the point) is not realistic.

## 2026-10-29 — Four-eyes and supplier IBAN trust

### Four-eyes counts everyone who prepared the invoice
- **Replaces** the rule that only the document's `reviewed_by` may not approve or reject.
- **Did:** with four-eyes on, a user who uploaded the document, edited its fields, resolved one of its checks or marked it reviewed can't approve or reject it (`invoices/four_eyes.py`, used by the status machine and by `allowed_actions`). Sending it back stays allowed. The involvement is read from the document's events, so it holds across send-back and reopen.
- **Why:** an invoice that goes straight to `awaiting_approval` has no reviewer, so its uploader could approve it alone; and a user who changed the IBAN or confirmed the changed bank details (C05) could approve after someone else clicked "mark reviewed". Both let one person get a payment approved.
- **Trade-off:** in an organisation with two members, the one who uploads can never approve; four-eyes can be switched off (the change is recorded since the audit events above).
