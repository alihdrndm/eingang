# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — Unreleased

The first version: an inbox for supplier invoices that validates e-invoices, turns every invoice into the same data, runs business checks, routes invoices through review and approval, and exports them for the accountant.

### Added

#### E-invoice formats (`einvoice`)

- Format detection for XRechnung (UBL and CII), ZUGFeRD/Factur-X hybrid PDFs with their profile, legacy ZUGFeRD 1, plain PDFs with a text layer and scans, with a format label for each.
- Safe XML parsing that refuses any DOCTYPE and never resolves entities or loads DTDs, also for XML embedded in PDFs.
- A canonical invoice model; UBL and CII parsers into it, and writers from it for both syntaxes.
- Validation with the official XSD and the EN 16931 and XRechnung Schematron rules from the pinned KoSIT configuration release, with SVRL parsing and the scenarios' custom rule levels.
- A static HTML rendering of XML invoices from the KoSIT visualisation stylesheets, with all scripts removed.
- Tests against the pinned ZUGFeRD corpus and every instance of the KoSIT XRechnung test suite.

#### API and data (`backend`)

- Typed settings read once from the environment; problem+json errors with documented codes; request ids; JSON request logs without query strings or personal data; `/healthz` and `/readyz`.
- Organisations, users with four roles, session authentication with CSRF, per-organisation scoping with an IDOR test over every object endpoint, and rate limits.
- Uploads with size, content-type and quota checks; document storage on the local disk or Supabase Storage (S3) with content-addressed keys.
- Document list with filters, ordering and paging; document detail, original file, invoice XML, extracted text and rendered view; soft delete.
- Business checks C01–C16 (duplicates, changed or invalid IBANs, arithmetic, VAT IDs, overdue invoices, an invoice addressed to someone else and others) and the document status machine with `allowed_actions` and their reasons.
- Review flow: edit fields, resolve checks with a note, mark reviewed, approve or reject with four-eyes, send back, reopen, retry.
- Suppliers with matching, IBAN history and confirmation of changed bank details.
- Exports of approved invoices as German CSV files and a ZIP bundle with the originals.
- Organisation settings, members, `/stats`, rule explanations and the `create-org` command.
- The OpenAPI schema, exported to `backend/openapi.json` and checked for freshness in CI.

#### Workflows (Temporal)

- `ProcessInvoiceWorkflow`: detection, validation, structured parsing, rendering, LLM extraction and comparison, supplier matching, checks and rule explanations, then a durable wait for review and approval signals with approval reminders.
- `MaintenanceWorkflow` on a daily schedule: deletes expired sandboxes, starts documents left unstarted, re-syncs workflows with the database and logs daily counts.
- Optional `MailboxPollWorkflow` that reads invoice attachments from an IMAP mailbox.
- `ensure_schedules`, which creates or updates the schedules idempotently.

#### LLM features

- One budgeted OpenAI client with a ledger row per call (never prompt or response text), a response cache, and lifetime, monthly, daily-public and per-sandbox budgets.
- Extraction of plain-PDF invoices with a confidence and verbatim evidence per field; comparison of a hybrid PDF's visible text with its XML; plain-language explanations of validation rules.
- Versioned prompts with fenced input as a defence against prompt injection; tests against recorded responses.

#### Sandbox

- A public sandbox per visitor with twelve fictional sample invoices, seeded from precomputed results without any LLM call or workflow, and deleted after `SANDBOX_TTL_HOURS`.
- The sample builder (`build-samples`) and the local seed (`pnpm seed`).

#### Evaluation

- An extraction evaluation on the pinned ZUGFeRD corpus comparing a label-anchored regex baseline with the LLM, with a seeded bootstrap confidence interval, cost and latency.
- A validation-parity comparison with the official KoSIT validator.
- Published results in `evals/latest.json`, served at `GET /api/v1/accuracy`, and described in `docs/EVALS.md`.

#### Frontend

- A Vue 3 app built from the design export: design tokens, self-hosted fonts and base components.
- Homepage with its interactive demo, sign-in and sandbox start.
- Inbox with status tabs, search, filters and uploads (one file per request, three at a time, live processing steps).
- Invoice review with the document viewer (PDF, rendered XML, XML source, extracted text), validation findings, checks, fields with confidence and evidence, lines, VAT breakdown and timeline; `j`/`k` navigation.
- Approvals, suppliers with IBAN history, exports, accuracy (in the app and public), settings with members and the AI budget meters, and a not-found page.
- Accessibility checks with axe and keyboard tests.

#### Tooling, packaging and deployment

- Root scripts `dev`, `verify`, `test:e2e`, `gen:api`, `db:reset` and `seed`; Ruff, mypy strict, import-linter contracts, per-package coverage thresholds; ESLint, Prettier, vue-tsc and a raw-colour check.
- CI with backend, frontend and end-to-end jobs.
- Dockerfiles for the API and the worker, a frontend image for the end-to-end stack, the `e2e` Compose profile and a Playwright smoke test from the sandbox to the export.
- Vercel configuration (`frontend/vercel.json`) and the deployment guide `docs/DEPLOY.md`, tested by the first deploy (Railway, Supabase and Vercel) with the measured proxy hop count.
- `CONTRIBUTING.md`, `SECURITY.md`, `NOTICE` and the documentation in `docs/`.

### Removed

- The earlier Vue 2 and Firebase application that this repository started from.

[0.1.0]: https://github.com/alihdrndm/eingang/commits/main
