# Verified vs assumed

Statements in the specification that were **not** verified against a real system. Each keeps its ID from the specification so it can be traced back. When one is verified (or proved wrong), its entry says so, with the date.

| ID | Assumption | Status |
|----|------------|--------|
| [E1](#e1) | Hybrid PDFs are not checked for PDF/A-3 conformance. | Assumed |
| [E2](#e2) | Vercel limits a proxied request body to about 4.5 MB. | Assumed |
| [E3](#e3) | BASIC and EXTENDED are checked against the EN 16931 rules only. | Assumed |
| [E5](#e5) | XRechnung 1.x and 2.x are checked against the EN 16931 rules only. | Assumed |
| [H1](#h1) | The daily maintenance keeps the free Supabase project from pausing; a paused project can be restored for free. | Assumed |
| [H2](#h2) | Railway's lowest usage hard limit is USD 10 per month. | Partly verified (11 Oct 2026): a USD 10 limit is set and stops all resources; the minimum is not shown |

There is no E4 or H3: the specification marks no other item as assumed.

## E1

**Hybrid PDFs are not checked for PDF/A-3 conformance.**

- **What is assumed:** a ZUGFeRD / Factur-X file whose embedded XML is valid can be treated as a valid e-invoice, even though its PDF part is not checked against PDF/A-3.
- **Why:** the check needs veraPDF, a Java tool, which is out of scope.
- **How the code handles it:** `backend/src/einvoice/pdf.py` takes the embedded XML out with `facturx.get_xml_from_pdf(..., check_xsd=False)`; only that XML is validated (`backend/src/einvoice/validate.py`). Nothing inspects the PDF container's conformance. Two corpus files show the effect: `wrongFilename.pdf` and `ZUGFeRD_2_fully_compliant_complete.pdf` are `valid` in Eingang although the corpus files them under `fail` (`docs/DECISIONS.md`, "Corpus `fail` files that are not rejected").
- **What the UI says:** the specification prescribes no text. The validation line names only the rules that ran on the XML.
- **Risk:** a real recipient may reject a non-conformant PDF/A-3 file that Eingang accepts.
- **How to verify:** run veraPDF (profile PDF/A-3b) on the hybrid PDFs of the ZUGFeRD corpus and compare its verdicts with Eingang's.

## E2

**Vercel limits a proxied request body to about 4.5 MB.**

- **What is assumed:** a request that Vercel rewrites to the Railway API (`frontend/vercel.json`, `/api/:path*`) may carry a body of at most about 4.5 MB.
- **Why:** uploads in production pass through that rewrite, so a larger body would fail at Vercel before it reaches the API.
- **How the code handles it:** each file may be at most 4 MB (`MAX_UPLOAD_BYTES=4194304` in `backend/src/eingang/config.py`), checked while the body is read (`backend/src/invoices/middleware.py`, `backend/src/invoices/uploads.py`; a larger file gets `413 FILE_TOO_LARGE`). The API accepts up to 10 files per request, but the frontend always sends **one file per request**, at most 3 in parallel, each with its own result row (`frontend/src/features/upload/uploadQueue.ts`, `frontend/src/features/upload/sendUpload.ts`).
- **What the UI says:** "PDF or XML, up to 4 MB each, 10 at a time." on the drop zone, and "Larger than 4 MB." on a refused file's row (`frontend/src/features/upload/UploadZone.vue`). The specification prescribes no text about the proxy.
- **Risk:** if the real limit is lower than 4 MB plus the multipart overhead, uploads near 4 MB fail at the proxy.
- **How to verify:** after deploying, upload a file just under 4 MB through the Vercel URL and check it is stored; compare with Vercel's documented request body limit for rewrites.

## E3

**BASIC and EXTENDED are checked against the EN 16931 rules only.**

- **What is assumed:** XSD plus the EN 16931 (CEN) Schematron is a good enough check for Factur-X / ZUGFeRD BASIC and EXTENDED invoices.
- **Why:** the profile-specific Factur-X Schematron is out of scope; the vendored KoSIT configuration contains only the EN 16931 and XRechnung rules.
- **How the code handles it:** `backend/src/einvoice/validate.py` runs the XSD and the EN 16931 rules for BASIC, EN 16931, EXTENDED and XRECHNUNG; it runs the XRechnung rules only for XRechnung 3.x (see E5). MINIMUM and BASIC WL are not EN 16931 invoices and get `not_applicable`. An EXTENDED invoice may therefore show EN 16931 findings that the Factur-X EXTENDED profile allows.
- **What the UI says:** "Checked against EN 16931 rules only." for every validated invoice whose profile is not XRechnung, which covers BASIC, EN 16931 and EXTENDED (`rulesLine` in `frontend/src/features/review-screen/validation.ts`). This matches the text the specification prescribes, with a closing full stop.
- **Risk:** EXTENDED invoices can show errors that are allowed by their profile; BASIC and EXTENDED errors that only the Factur-X rules catch are missed.
- **How to verify:** run the official Factur-X Schematron for BASIC and EXTENDED on the corpus files of those profiles and compare with Eingang's findings. The parity evaluation (`evals/parity.py`) does not cover this, because the KoSIT validator uses the same EN 16931 rules.

## E5

**XRechnung 1.x and 2.x are checked against the EN 16931 rules only.**

- **What is assumed:** XRechnung 1.x and 2.x invoices can be checked with XSD and the EN 16931 rules alone.
- **Why:** the vendored rules are for XRechnung 3.0 (configuration release `v2026-01-31`, XRechnung 3.0.2); applying them to older versions would report rules that did not exist then.
- **How the code handles it:** `backend/src/einvoice/detect.py` reads the version from the specification identifier (BT-24, for example `urn:xoev-de:kosit:standard:xrechnung_2.3` gives profile `XRECHNUNG`, version `2.3`). `backend/src/einvoice/validate.py` runs the XRechnung Schematron only when the version starts with `3`; otherwise the invoice gets XSD and EN 16931 only.
- **What the UI says:** "XRechnung 2.x: checked against EN 16931 rules only." for an XRechnung invoice whose identifier is the older KoSIT standard (`urn:xoev-de:kosit:standard:xrechnung`) or names version 1.x or 2.x (`rulesLine` in `frontend/src/features/review-screen/validation.ts`). This matches the text the specification prescribes, with a closing full stop. The same text is shown for 1.x.
- **Risk:** findings that only the XRechnung 1.x/2.x CIUS rules would raise are missed.
- **How to verify:** validate XRechnung 1.x/2.x invoices with the KoSIT validator and the configuration release made for that XRechnung version and compare with Eingang's findings.

## H1

**The daily maintenance keeps the free Supabase project from pausing; a paused project can be restored for free.**

- **What is assumed:** Supabase Free pauses a project after about a week without activity, a daily database query counts as activity, and restoring a paused project costs nothing.
- **Why:** the public demo runs on Supabase Free; a paused database takes the demo offline.
- **How the code handles it:** the schedule `daily-maintenance` (03:10 UTC, `backend/src/eingang/schedules.py`) runs `MaintenanceWorkflow`, whose last step `log_daily_stats` (`backend/src/eingang/maintenance_activities.py`) counts documents per status in the database.
- **What the UI says:** nothing; `docs/DEPLOY.md` explains how to restore a paused project from the Supabase dashboard, and notes that the maintenance does not run while the services are paused.
- **Risk:** the demo is offline until the owner restores the project.
- **How to verify:** keep the deployment running for more than a week with no visitors and check the project stays active in the Supabase dashboard.

## H2

**Railway's lowest usage hard limit is USD 10 per month.**

- **What is assumed:** Railway lets a workspace set a usage hard limit, the lowest allowed value is USD 10 per month, and Railway stops the services instead of charging more when it is reached.
- **Why:** `docs/DEPLOY.md` step 0 tells the owner to set this limit first, so the monthly bill is capped.
- **How the code handles it:** not in code; it is an account setting. `docs/DEPLOY.md` step 0 also sets a usage email alert at USD 4.
- **What the UI says:** nothing in the app; `docs/DEPLOY.md` step 0 tells the owner to check the page for the current minimum.
- **Risk:** if the minimum is higher, the cap on the bill is higher too.
- **How to verify:** open Railway's workspace usage settings page and read the minimum hard limit before deploying.
- **Checked (11 Oct 2026):** Railway's usage page (**Usage → Update limits**) shows a compute hard limit of USD 10, described as "All resources stopped at this amount", and an email alert at USD 5 (`docs/DEPLOY.md` suggests USD 4). The dialog does not state the lowest allowed value, so that part stays assumed.
