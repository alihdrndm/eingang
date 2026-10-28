# Design map

Where each part of the design export (`design/export/`) lives in the code, and every place where the code deliberately differs from the design. The design is the source of truth for how things look and read; `HANDOFF.md` is the source of truth for behaviour and data.

First draft, written with the base components and the app shell. Screens are added as they are built.

## Tokens and global styles

| Design | Code | Notes |
|---|---|---|
| Colour tokens (`--desk` … `--focus`) | `frontend/src/styles/tokens.css` | Same names and values as the design system. The only file allowed to hold raw hex values (`scripts/check-hex.mjs`). |
| Fonts (`--font-head`, `--font-sans`, `--font-mono`, `--font-hand`, `--font-stamp`) | `frontend/src/styles/tokens.css`, `frontend/src/styles/fonts.ts` | Self-hosted from `@fontsource`: Familjen Grotesk 700, Instrument Sans 400/500/600, JetBrains Mono 400/500, Barlow Condensed 700, Caveat 600. |
| Radii (`--r-sm`, `--r-md`, `--r-lg`, `--r-pill`) | `frontend/src/styles/tokens.css` | 6 / 8 / 12 / 999 px. |
| Shadows (`--shadow-window`, `--shadow-sheet`) | `frontend/src/styles/tokens.css` | Plus `--shadow-raised` (`0 1px 0 --line-strong`), the raised look of the active rail item and the selected segmented tab. |
| Spacing scale 4 … 72 | `frontend/src/styles/tokens.css` (`--space-4` … `--space-72`) | The design gives the steps as numbers only; the token names are ours. |
| Type scale 12 / 13 / 14 / 16 / 17 / 21 px, section and hero clamps | `frontend/src/styles/tokens.css` (`--fs-*`, `--lh-*`) | Names are ours, values are the design's. |
| Control and layout sizes (28 / 32 / 44 px controls, 48 px rows and top bar, rail 88 / 64 px, dialog 480 / 520 / 680 px, …) | `frontend/src/styles/tokens.css` (`--size-*`) | Names are ours, values are the design's. |
| Motion (easings `draw`, `out`, `arrive`; 900 ms spinner turn) | `frontend/src/styles/tokens.css` (`--ease-*`, `--dur-*`) | |
| Dialog backdrop "`--ink` at 32 %" | `--backdrop` in `tokens.css` | `color-mix()` of `--ink`. |
| Page base (body font, focus ring, selection, reduced motion) | `frontend/src/styles/base.css` | Light theme only. With `prefers-reduced-motion` all animations and transitions end at once; spinners stand still. Adds the `.sr-only` utility. |

## Components

| Design component | Code | Notes |
|---|---|---|
| Icons (`<e-icon>`, `eingang-ui.js`) | `frontend/src/components/ui/Icon.vue`, `frontend/src/components/ui/icons.ts` | All 41 design icon names map to `lucide-vue-next` icons with a 1.5 px absolute stroke. Hidden from assistive tech unless given a `label`. |
| Button | `frontend/src/components/ui/Button.vue` | primary / secondary / ghost / danger; sm 28, md 32, lg 44; leading icon; loading (spinner, label set by the caller, e.g. "Approving…"); real `disabled`. `disabledReason` adds the lock icon and a tooltip linked with `aria-describedby` (states board "permission-disabled buttons"). |
| Input | `frontend/src/components/ui/TextInput.vue` | Named `TextInput` in code. Label above, hint or error below (error with the octagon icon), `mono` for IBANs and numbers, `lg` for phone. |
| Textarea | `frontend/src/components/ui/TextArea.vue` | Named `TextArea` in code. Optional `<length> / <max>` counter (polite live region) as in the review dialogs; error and hint together. |
| Select | `frontend/src/components/ui/Select.vue` | See deviation 1. `inline` gives the inbox filter-bar look ("Supplier [select]"). Disabled shows the lock icon. |
| Badge | `frontend/src/components/ui/Badge.vue` | count, selected count, label, Sample (dashed), overdue. |
| StatusChip | `frontend/src/components/ui/StatusChip.vue` | All eight document statuses with the design's label, icon and colour pair; the Processing icon spins. Typed from the generated OpenAPI `DocumentStatusEnum`. |
| SeverityIcon | `frontend/src/components/ui/SeverityChip.vue` | Named `SeverityChip` in code. Checks: Block / Warning / Info; validation issues: Error / Warning / Info. `iconOnly` labels the icon. |
| FormatChip | `frontend/src/components/ui/FormatChip.vue` | Squared corners; icon chosen from the API's format label (XML → file-code, ZUGFeRD and hybrid → paperclip, plain and scanned PDF → file-text); ZUGFeRD tooltip; "· credit note"; long labels wrap. |
| `<e-bars>` (inside ConfidenceChip) | `frontend/src/components/ui/ConfidenceBars.vue` | Bars are `aria-hidden`; a screen-reader text says "High/Medium/Low confidence" unless the caller passes an empty label because a visible word is next to it. The full ConfidenceChip (High / Medium / Low / "Edited by …") is drawn inside `FieldsPanel.vue`. |
| DataTable | `frontend/src/components/ui/DataTable.vue` | Real `<table>` with caption and `th scope="col"`; sticky header on `--bar`; 48 px rows (see deviation 2); sortable header buttons with `aria-sort`; rows open on click, Enter or Space; `aria-selected` on selected rows; mono right-aligned amounts; cells through `cell-<key>` slots. Sorting itself is the caller's (the API sorts). |
| Tabs | `frontend/src/components/ui/Tabs.vue` | underline and segmented; WAI-ARIA tabs (roving tabindex, ←/→/Home/End, automatic activation, disabled tabs skipped); count badges; disabled with lock icon and reason. |
| Dialog | `frontend/src/components/ui/Dialog.vue` | Native `<dialog>` opened with `showModal()` (falls back to the `open` attribute). Labelled by its title; focus moves to the first field, stays inside, returns to the opener; Esc, Close and backdrop click close it. Sizes 480 / 520 / 680 px; full width minus 16 px margins on phones. |
| Toast | `frontend/src/components/ui/Toast.vue`, `frontend/src/components/ui/useToast.ts` | One at a time, bottom right. Success and info: `role=status`, polite, leave after 4 s (see deviation 3); errors and rate limit: `role=alert`, stay until dismissed; optional action such as "Try again". `useToast().show()` from anywhere. Mount `<Toast />` once in the app layout. |
| Skeleton | `frontend/src/components/ui/Skeleton.vue` | Static `--line` bars laid out by grid tracks so they match the content; optional pane-header bar; `aria-busy` with a label; appears only after 300 ms. |
| EmptyState | `frontend/src/components/ui/EmptyState.vue` | Icon disc, title, what it means, one action slot. |
| ErrorState | `frontend/src/components/ui/ErrorState.vue` | Shows the API problem's `title` and `detail` and a "Try again" button that emits `retry`; `role=alert`. |
| Stamp (lg, md, mark, chip) | `frontend/src/components/ui/Stamp.vue`, `frontend/src/components/ui/ink.ts` | lg 44 px and md 22 px lettering with every measure as a fraction of it (em); German date "09. OKT. 2026"; `role=img` named "Eingang stamp, 9 October 2026". `mark` is the square "E" (24 / 28 / 40 px). `chip` is the received chip ("07 Oct, 14:30"). The shared ink filter is added to the page once. Used by the top bar (mark) and the inbox drag overlay (lg). See deviation 4. |
| AppShell, TopBar, Rail | `frontend/src/components/shell/AppShell.vue` | Top bar: mark + "Eingang" / organisation / search with "/" hint (Esc hint while focused) / "Sandbox, <n> h left" / user-menu slot. Rail: Inbox, Approvals, Suppliers, Exports, Accuracy, Settings as `RouterLink`s to `/app/…`, active item raised with `aria-current="page"` (invoice and supplier detail pages count as their section). ≥ 1280 px labels; 768–1279 px icons only with tooltips; ≤ 767 px phone header (mark, page title, phone-actions slot, user menu) and bottom tab bar of five with More. Slots: page (default), `banner` (SandboxBanner), `user-menu`, `phone-actions`. No data fetching. See deviations 5 and 6. |
| DocumentViewer | `frontend/src/features/viewer/DocumentViewer.vue`, `PdfView.vue`, `TextView.vue`, `pdf.ts`, `api.ts` | Segmented tabs Document / XML / Text; tabs that don't apply are disabled with the lock and the design's reason ("This PDF has no XML inside", "This invoice is XML only, there is no PDF text"). PDFs: PDF.js (loaded on demand, worker from the app's origin) renders one page into a canvas from the bytes of `GET …/file`; page buttons (≤ 8 pages, plus previous / next), zoom 50–200 %, download link. XML invoices: `GET …/visualization` in an `<iframe sandbox>` (no scripts) with a title; when it is `404 NOT_AVAILABLE` the Document tab is disabled and the viewer opens the XML tab. Text tab: the PDF text with line numbers; the selected field's evidence is a `<mark>` in `--hl` with a 2 px `--stamp` ring, its line on `--soft`, scrolled into view. |
| CodeView | `frontend/src/features/viewer/CodeView.vue`, `xml.ts` | Line numbers; element names `--stamp`, attributes `--warn-text`, values `--ok`, brackets `--muted`, text `--ink`. The highlighter makes text tokens, never HTML. |
| ValidationChip | `frontend/src/features/review-screen/ValidationChip.vue`, `validation.ts` | "Valid", "Valid with warnings", "Invalid · n errors", "Not applicable — plain PDF" (or "Not applicable" for XML formats). |
| IssueList | `frontend/src/features/review-screen/ValidationSection.vue` | Issues grouped by severity (Errors, Warnings, Information); rule ID in mono, plain explanation and fix hint, "Show official message" / "Hide official message" (`aria-expanded`), official message verbatim (`lang="de"` for XRechnung rules), location, "Show in XML". |
| LinesTable, TaxBreakdown, Timeline | `frontend/src/features/review-screen/LinesSection.vue`, `VatSection.vue`, `TimelineSection.vue`, `timeline.ts` | Timeline: every `Event.Type` in plain words, newest first, with a "Waiting for review / approval" entry on top; comments and notes quoted; IBAN values never shown. |
| IBAN tag | `frontend/src/features/suppliers/IbanTag.vue`, `iban.ts` | "Known account" (`known`), "Confirmed change" (`confirmed`), "New account" (`new`) with the design's icons and colour pairs (design decision 7); `outlined` gives the bordered look of the supplier detail. Used by Approvals and the supplier detail. |
| Switch | `frontend/src/features/settings/SettingSwitch.vue` | `role="switch"` button with the On / Off word, description and lock reason; off, on, hover, focus, disabled, disabled-on. Lives in the settings feature until another screen needs it. |
| MeterBar | `frontend/src/features/settings/MeterBar.vue` | `role="meter"` with "$0.42 of $2.00" as text and value text; under (`--ink`), near the limit from 80 % (`--warn`), reached at 100 % (`--block`). |
| ConfidenceChip, FieldRow, EvidencePopover | `frontend/src/features/review/FieldsPanel.vue`, `fields.ts` | Fields grouped Invoice / Supplier / Buyer / Payment / Totals with the EN 16931 term in mono; chips High / Medium / Low / "Edited by <name>"; low rows on `--block-soft` with a hint; the eye shows the evidence snippet (value marked in `--hl`) on hover and focus and pins it on click; "Show in document"; pencil → inline edit with Save / Cancel (Enter / Escape), focus back on the pencil afterwards, amounts as `1.190,00` or `1190.00` and dates as `DD.MM.YYYY` or `YYYY-MM-DD` (sent in the API's format), validation and the API's field errors; credit-note amounts negative. |
| CheckList, CheckCard, DiffView | `frontend/src/features/review/ChecksPanel.vue`, `checks.ts` | Open checks first, worst first; severity chip and check ID; C02/C03 link the earlier invoice; C11 links its source; C09 shows the PDF/XML differences as a table; Resolve… / Accept anyway… with the API's reason as visible text when disabled; resolve dialog with the note (5–500, counter, "Write at least 5 characters."); resolved: "Resolved by …" / "Accepted anyway by …" and the quoted note. |
| DropZone, UploadProgressRow, StepProgress | `frontend/src/features/upload/UploadZone.vue`, `UploadFileRow.vue`, `uploadQueue.ts`, `sendUpload.ts` | "Upload invoices" dialog with the drop area and "Choose files"; window drag overlay (Esc closes); one request per file, three at once; refused files get their own row and reason; per-file progress, then the steps Detect / Validate / Read / Check (done, current, failed, not run) with a polite status; Retry, Remove, Open. |
| SandboxBanner | `frontend/src/views/AppFrame.vue` | "Sandbox · deleted in <hours> h · sample data, nothing is real", on every app page (see Shell differences). |
| Homepage acts | `frontend/src/features/homepage/ActOne.vue`, `ActTwo.vue`, `motion.ts` | See Homepage differences. |

### Not built yet

PaneHeader, IconButton, Checkbox and SearchField as standalone components (each screen draws its own pane header and icon buttons; the top-bar search lives in AppShell), ApprovalCard (the Approvals phone cards are part of `ApprovalsView.vue`), favicon.

## Screens

| Design screen | Code | Notes |
|---|---|---|
| App shell (Inbox and Screens pages) | `frontend/src/components/shell/AppShell.vue` | |
| Inbox (`Eingang App Inbox`) | `frontend/src/views/InboxView.vue`, `frontend/src/features/inbox/` | Tabs Needs review / Awaiting approval / Approved / Exported / Failed / All (default Needs review) with counts from `/stats`; supplier and format filters, "Clear filters", sortable Received / Gross / Due; columns Received (received chip), Supplier, Number, Gross (credit notes negative), Due (overdue marker), Format, E-invoice, Validation, Checks (block / warning counts), Status (with the live processing step). Search matches are marked. Empty state per tab with the design's copy; "No invoices match" with "Clear search and filters"; skeleton rows; error with "Try again". Phones get cards, the search field and 44 px filters. Upload button, drop overlay and progress rows come from `frontend/src/features/upload/UploadZone.vue`. All list state is in the URL (`tab`, `q`, `supplier`, `format`, `ordering`, `page`); see the Inbox differences below. |
| Invoice review (`Eingang App Review`, A / B / C) | `frontend/src/views/InvoiceReviewView.vue`, `frontend/src/features/review-screen/`, `frontend/src/features/viewer/`; Checks and Fields: `frontend/src/features/review/` | Status header (back link, j / k hint, supplier, status chip, number, received chip, format and validation chips, gross with a minus sign for credit notes (type 381), due date) with `allowed_actions` as buttons in the order Mark reviewed, Approve, Reject…, Send back…, Reopen, Retry, Delete…; disabled ones keep the button and show the API's reason as text and as the button's description. Reject and Send back need a 5–500 character note ("Write at least 5 characters."); Delete asks to confirm. Then the "Only the first 12,000 characters were read" note, Validation, Checks, Fields, Lines, VAT breakdown, Timeline. Four data states: skeleton of both panes, error with "Try again", the not-found page for a 404, success; polls every 2 s while received / processing. ≥ 1280 px two panes; 768–1279 px data first, "Show document" / "Hide document" below; ≤ 767 px tabs Data / Document / Activity. See the review differences below. |
| Sign in (`Eingang App Sign in`) | `frontend/src/views/SignInView.vue` | Card with Email and Password (show / hide), field errors ("Enter your email address.", "Enter your password."), "Signing in…", the API's problem detail for wrong credentials, "Open the sandbox instead" and the line under it. |
| Homepage (`Eingang Homepage`, `Homepage Phone`) | `frontend/src/views/HomeView.vue`, `frontend/src/features/homepage/` | Header, hero, Act 1, Act 2, facts strip with sources, close, footer; below 1100 px the phone layout centred at 680 px. "Open the sandbox" calls `POST /sandbox`; a 429 shows the API's detail; the expired-sandbox notice is shown once. axe: no serious or critical findings at 1440 and 390 px. See Homepage differences. |
| Not found (?screen=notfound) | `frontend/src/views/NotFoundView.vue` | "404 / Nothing at this address", one action "Go to the Inbox". |
| Approvals (`Eingang App Screens` ?screen=approvals) | `frontend/src/views/ApprovalsView.vue`, `frontend/src/features/approvals/` | `GET /documents?status=awaiting_approval&ordering=due_date`; header with the count and, when four-eyes is on, "Four-eyes is on: nobody approves an invoice they reviewed themselves."; rows: supplier and number (links to the review), gross (credit notes negative), due, `•••• <last4>` with the IBAN tag, open warnings, reviewed by, Reject… / Approve from `allowed_actions`. Disabled decisions keep the button (lock) and show the API's reason as text under the buttons. Approve runs at once ("Approving…", toast "You approved …."); Reject… opens "Reject invoice …?" with "Reason (required)", 5–500 characters, "Write at least 5 characters.". Empty: "Nothing waiting for you". ≤ 767 px: stacked cards with 44 px buttons. Shared pane styles: `features/approvals/pane.css`. |
| Suppliers list (?screen=suppliers) | `frontend/src/views/SuppliersView.vue` | `GET /suppliers` with `?q=` (search field in the pane header, 300 ms debounce, kept in the URL with `page`); columns Supplier (with VAT ID), Invoices, First invoice, Last invoice; rows open the supplier. Empty: "No suppliers yet"; no match: "No suppliers match" with "Clear search". |
| Supplier detail (?screen=supplier / supplier-confirmed) | `frontend/src/views/SupplierDetailView.vue`, `frontend/src/features/suppliers/` | Back link, name, "<n> invoices since <date>". IBAN history: grouped IBAN with its tag; untrusted ones first on `--block-soft`: "First seen on <number>, <date> · Not confirmed yet" and "Open the check on <number>" (to that invoice's review, where the C05 check is); confirmed ones on `--ok-soft`: "Confirmed by <name>, <date> · ‘<note>’"; known ones "Known account · first seen <date> on <number>.". Invoices: received chip, number, invoice date, gross, status; rows open the review. A 404 shows "No such supplier". |
| Exports (?screen=exports) | `frontend/src/views/ExportsView.vue`, `frontend/src/features/exports/` | "Export approved invoices" with "<n> invoices ready" from `/stats` `by_status.approved`; the three formats as radio cards (design labels and help); "Export <n> invoices" / "Exporting…" / disabled "Export" when nothing is ready. `POST /exports` → toast "Export ready. <n> invoices moved to Exported." with a Download action, plus an inline download link; `409 NOTHING_TO_EXPORT` shows the API's detail in the card. Approvers and viewers see the button disabled with the reason as text. Past exports: `GET /exports` (paged) with Created, Format, Invoices, By and a `download` link; "No exports yet. Your first export appears here." |
| Settings (?screen=settings) | `frontend/src/views/SettingsView.vue`, `frontend/src/features/settings/` | Organisation (Name, VAT ID), Review and approval (four-eyes switch, Duplicate window and Reminder after with the design's hints); "Save changes" enabled only when something changed, `PATCH /organization` with only the changes, toast "Settings saved.". Non-admins: fields disabled with "Only admins can change settings."; sandbox: only Name editable, the rest "Not available in the sandbox.". Members (admins, not in a sandbox): role select and Deactivate / Reactivate (not for yourself: "You can’t change your own role."), "Invite member" dialog (email, name, role; "Choose a role before you invite someone.") that shows the one-time password once with "Copy password" and forgets it on close. AI budget: two meters from `/stats` `llm`, and "<n> AI calls left in this sandbox." when present. |
| Accuracy, app and public (?screen=accuracy / accuracy-public) | `frontend/src/views/AccuracyView.vue`, `frontend/src/views/AccuracyPublicView.vue`, `frontend/src/features/accuracy/` | One `AccuracyReport.vue` renders `GET /accuracy` for both: title, "Results of the published test run of <date>, on <n> invoices.", Method (the design's three steps, dataset, corpus commit, "Full method on GitHub" → `docs/EVALS.md`), critical fields correct per system with the 95 % interval, Per field (Correct / Invented / Missing of the AI system plus each other system's Correct; "—" where a rate is absent), agreement with KoSIT (verdicts, rule sets, files, excluded), AI cost per invoice per system. `404 NOT_AVAILABLE`: "No results published yet" with the GitHub link; errors show the problem with "Try again". The public page has the mark, "Eingang" and "Open the sandbox" instead of the app shell. |

## Deliberate differences from the design

1. **Select uses the native `<select>`.** The design system shows a custom listbox when open; the inbox screens themselves use a styled native select. The native control keeps keyboard and screen-reader behaviour for free; the closed state matches the design, the open list is the browser's.
2. **DataTable rows are 48 px.** The design system page says 44 px, the handoff notes say 48 px; the handoff notes win by their own rule.
3. **Toasts leave after 4 s.** The design system says 5 s, the handoff notes 4–5 s, the design's code 4 s.
4. **Stamp months.** The design shows only OKT., MÄRZ and SEP.; the other months follow the same style: JAN., FEB., MÄRZ, APR., MAI, JUNI, JULI, AUG., SEP., OKT., NOV., DEZ. The received chip shows the time in the viewer's time zone.
5. **Nav counts.** The design's rail has no counts. AppShell accepts optional counts per item and shows them as a count badge on the item's icon; nothing shows when no count is passed.
6. **More menu on phones.** The design says "More opens Accuracy and Settings" without drawing it. It opens a small list above the tab bar in the popover style of the design (`--win`, `--line` border, `--shadow-window`); Esc closes it and returns focus. The phone header shows the page title as text; the page keeps its own `h1`.
7. **Disabled buttons with a reason** keep the real `disabled` attribute (handoff notes) rather than the states board's focusable `aria-disabled` span; the reason is the tooltip and the button's description.

### Inbox differences

- **No selection.** The design has row checkboxes and "N selected · Clear selection" but no bulk action, and the API has no bulk endpoint the inbox would call, so selection is left out.
- **Tab counts are totals.** The design counts rows matching the current search; the counts come from `/stats` (`by_status`, All = every status) and ignore search and filters.
- **Only Received, Gross and Due sort.** The design also sorts Supplier, Number and Status; the API orders by `-received_at`, `received_at`, `-gross_total` and `due_date` only. Gross sorts high to low and Due soonest first in one direction; Received toggles, starting newest first.
- **Format filter options.** The design lists the formats present in its sample data. The code lists every label the API gives a supported file, then any other label seen in the list (for example "… · credit note"), since the API filters on the exact label.
- **Search on wide screens** stays in the top bar (as designed); the page shows the active search as a removable “text” chip next to the filters, because the top bar does not show the inbox's current query. Phones get the designed search field above the filters.
- **Validation chip** reads "Invalid" without the error count, and the Checks cell has no per-check tooltip: the list endpoint gives neither the number of errors nor the check messages.
- **Pager.** The design shows one short page; the list is paged by 25 with "1–25 of 60", Previous and Next under the table.
- **Empty All tab** offers "Upload invoices", which opens the upload zone's own file picker.

### Invoice review differences

- **j / k list.** The design cycles three sample invoices. The code walks the inbox list the user came from: the inbox passes its `GET /documents` query as `?from=…` (see `features/inbox/listParams.ts`); the review reads that page from the query cache, or fetches it once if it is not cached. Without `from`, the most recently loaded cached list containing the invoice is used, else the unfiltered list. Only that page (25 invoices) is walked; at the ends it wraps round, and the toast then starts with "Back to the start of the list." / "Back to the end of the list." (not in the design). Keys are ignored while typing, with a modifier, or while a dialog is open.
- **Approve has no comment.** The design approves at once; the API's optional comment is sent empty.
- **Toast after Mark reviewed** reads "Marked reviewed. It goes to approval next." The design names the approver ("Jonas Brandt approves next."), which the API does not tell the reviewer. Send back, Reopen, Retry and Delete have no designed copy; theirs is ours.
- **Send back and Delete dialogs** are not in the design; they reuse the reject dialog's layout.
- **Issue header** shows the severity and rule ID only: the API's explanation has no short title (the design's "Buyer reference (Leitweg-ID) missing"). Issues without a stored explanation show the official message directly. The issues are grouped under "Errors", "Warnings" and "Information".
- **Validation line** names the KoSIT configuration release parsed from the API's `engine` ("Checked with the official XRechnung rules (KoSIT 2026-01-31)."); other engines are named as given.
- **Evidence highlight** is in the Text tab only. Selecting a field that has evidence switches a PDF's viewer to the Text tab; the PDF canvas and the XML tab are not highlighted.
- **Timeline wording** follows the design where it has an example ("Received", "Checked: valid e-invoice", "Checked: invalid, 1 error (BR-DE-15)", "Read by AI: plain PDF, 7 fields, 1 low confidence", "Marked reviewed by …", "Approved by …", "Rejected by …"); the other event types are ours. The "Checked" and "Read by AI" entries are written from the document's current validation and confidence data.
- **Phone layout.** The page shows its own back link to the Inbox; the shell's bottom tab bar stays (the design hides it on this screen), and the actions sit in the header with 44 px buttons instead of a separate action bar at the bottom.
- **PDF pages** are buttons in the viewer's tool row rather than thumbnails (the design's review has single-page samples and no thumbnail strip).

- **Fields of an XML invoice are editable** in `needs_review` for admins and accountants. The design makes them read-only ("Read-only: the XML is the invoice."); HANDOFF allows editing any invoice in review, and behaviour follows HANDOFF. The note then reads "From the XML. The XML is the invoice."; the read-only sentence is shown when editing is not allowed.
- **Visualisation note from KoSIT.** The rendered XML comes from KoSIT's own stylesheet and is shown in a sandbox without scripts. Its scripts, tab bar and `<noscript>` line ("The display of content on this page is limited without JavaScript.") are removed and every section is shown at once. It is KoSIT's page, not ours; the XML tab shows the source.

### Homepage differences

- **BASIC WL source label.** The design labels the link "Source: BMF letter, 15 Oct 2024", but the page it links is ELO's summary of that letter; the label now says so ("Source: ELO summary of the BMF letter, 15 Oct 2024"), as HANDOFF names it.
- **Hero art** is a simpler composition of the same elements (XML sheet with the highlighted amount, the plain-PDF sheet with the stamp, the red-pen "XML inside?"); the drawn pen strokes and highlighter swipe are not animated.
- **Red-pen notes** wipe in when their act is 30 % in view (shown at once with reduced motion); the drawn circles and arrows are left out.
- **Headline:** option 1 of the design's three ("Some of our invoices are e-invoices. We couldn't tell you which."). The tweaks (headline, motion, ink texture) are design-tool controls and are not built.

### Approvals differences

- **IBAN tag words** follow the design's decision 7 ("Known account", "New account") rather than HANDOFF's "same as before" / "new".
- **Open warnings** count the open warning checks and, when any, the open blocking checks ("1 blocking check, 2 warnings"); the list has no info-check count, so the design's "1 info" never shows.
- **The four-eyes reason** is the API's sentence; the design's lead-in "Entered by hand and marked reviewed by you." is not known to the list. The Design-only Krause row is not built.
- **The reject dialog** names the reviewer from `reviewed_by_name` ("Anna Weber sees your reason …"), else "Your reason is saved in the invoice’s activity.".
- **Pager** under the list when more than 25 invoices wait.

### Suppliers differences

- **List columns.** The design's "Open" (per-status counts) and "Bank account" columns are left out: `GET /suppliers` has neither. The list shows the VAT ID under the name and the first and last invoice dates.
- **Search** is a field in the pane header (the design has none on this screen); `?q=` and `page` live in the URL.
- **IBAN history.** "Also on …" is left out (the API does not say which invoices carry which IBAN). The invoice an IBAN was first seen on comes from the API's `first_seen_document_id`; if that invoice was deleted, the line has no number and no link.
- **Invoices table** shows Received, Number, Invoice date, Gross and Status; Format and Bank account are not in the supplier detail response, and amounts carry the API's sign (no type code there).

### Exports differences

- **ZIP help text** reads "Both CSVs plus every original XML and PDF." (the design says "The CSV plus …"; the bundle holds both CSVs).
- **Format choice** uses native radio inputs styled as the design's cards, in a fieldset with the legend "Format".
- **Download after export** is both the toast's action and a line in the card. The Design-only past export row is not built.
- **Role** comes from the session (only admins and accountants may `POST /exports`); others get the disabled button with "Your role (<role>) can't export. Admins and accountants can.".

### Settings differences

- **Members in a sandbox** show only "Not available in the sandbox." with the disabled "Invite member": `GET /members` answers `403 SANDBOX_RESTRICTED` there, so the design's two inactive sample people can't be listed.
- **No "Sample" badge** on the AI budget: the meters show the real figures from `/stats`, with a line on what the budget pays for.
- **Days fields** carry the unit in their label ("Duplicate window (days)") instead of a suffix inside the field; values are checked (1–365, 1–60) before saving.
- **Members table and invite dialog** are ours (the design draws only disabled sample rows).
- **"You have unsaved changes."** appears next to Save when the form is dirty.

### Accuracy differences

- **Real numbers.** The page shows `GET /accuracy` as published; the design's "Every number on this page is a sample." note, "Sample" badges and 0.00 placeholders are replaced by the run date and the measured values (percentages with one decimal).
- **Critical fields** are named as measured (HANDOFF "Extraction accuracy"): invoice number, invoice date, gross and, when present, the IBAN. The design's line also lists the VAT ID, which the metric does not include.
- **Several systems.** The headline lists every system in the report; the per-field table details the first non-baseline system (or the baseline when it is the only one) with a "<system>, correct" column for each other system.
- **Method step 2** says "The reader sees only the PDF" (the baseline is scored the same way); dataset name and corpus commit are added.
- **Public header** has a working "Open the sandbox" button (`POST /sandbox`; errors under the header).

### Shell differences

- **Sandbox banner on every app page.** The design shows it on the Inbox and Settings only; HANDOFF wants it on every app page in a sandbox.

## Screens not in the design

None: every screen in HANDOFF has a page in the export. Two states are not designed and reuse the nearest designed screen:

- **Expired sandbox** shows its message ("This sandbox has expired. Open a new one.") on the homepage, under the hero's buttons.
- **Review B with the LLM's fields** (confidence chips and evidence on a plain PDF) can't be captured from the seed yet: S08 is seeded with check C12 ("Automatic extraction is switched off") until the M5 precompute runs, which needs the owner's approval for a paid call. The fields form is covered by component tests with LLM data; `design/compare/review-b-llm-*.png` will be retaken after the precompute.
