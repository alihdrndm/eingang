# Evaluation

Two questions, measured separately:

1. **Extraction:** how well are invoice fields read from the visible text of a PDF?
2. **Validation:** does Eingang accept and reject the same e-invoices, for the same rules, as the official KoSIT validator?

The headline numbers are in `evals/latest.json` (served at `GET /api/v1/accuracy`). Each run also writes a report to `evals/reports/`.

## Extraction accuracy

### Dataset

The ZUGFeRD corpus (`github.com/ZUGFeRD/corpus`), pinned at commit `d891458e9822e34271a5438497bf924e89955979`. The dataset includes every PDF under a folder named `correct` or under `XML-Rechnung/FX/` that meets three conditions:
- its embedded XML is CII;
- that XML parses to a canonical invoice;
- the PDF has a text layer.

Identical files are counted once.

- **104 documents.** By language: German 94, English 5, French 5. By profile: EN 16931 69, EXTENDED 10, XRECHNUNG 7, BASIC 6, MINIMUM 6, BASIC WL 5, unknown 1.
- **21 PDFs skipped:** all of `ZUGFeRDv1/correct`, whose embedded XML is not CII.
- **Ground truth:** the parsed embedded XML.
- **Input:** only the PDF's visible text, read the same way the application reads it: the first six pages, cut at 12,000 characters. No input reached the cut; inputs range from 736 to 4,166 characters.
- The list of documents is `evals/data/manifest.json`. The corpus itself is not committed.

### Scoring

- **Fields scored:** invoice number, issue date, due date, currency, seller name, seller VAT ID, payee IBAN, buyer name, net total, tax total, gross total, payable amount.
- **Matching:**
  - Dates, currency, IBAN, VAT ID and amounts must match exactly after normalisation.
  - The invoice number matches ignoring case and whitespace.
  - Names match when `rapidfuzz.fuzz.token_set_ratio` is at least 90.
- **Per field:**
  - **Accuracy** is measured over the documents whose truth has the field.
  - **Hallucination** is a value predicted where the truth has none.
  - **Abstention** is no value predicted where the truth has one.
- **Per document**, *critical fields correct* means the invoice number, the issue date, the gross total and, when the truth has one, the IBAN are all right. It is reported with a 95% bootstrap interval (1,000 resamples, seed 42).

### Systems and results

| System | Critical fields correct | 95% CI | Cost |
|--------|------------------------:|--------|-----:|
| `regex-baseline` | 21.2% | 13.5%–28.8% | $0 |
| `llm:gpt-5-nano` (prompt `extract_invoice.v1`) | 65.4% | 56.7%–74.0% | $0.0334 ($0.000321 per document) |

- **`regex-baseline`** (`evals/baseline_regex.py`) uses label-anchored regular expressions for German, English and French invoices, with no LLM.
  - Per field: gross total 93%, payable amount 93%, IBAN 91%, VAT ID 89%, tax total 88%, net total 84%, issue date 75%, invoice number 34%, seller name 32%, buyer name 25%, due date 9%.
  - The invoice number is what holds the critical score down: most corpus PDFs print it inside a sentence ("Handelsrechnung (380) Nr. 471102 vom …") rather than after a label.
  - **Honesty note:** the first version tried labels in text order and missed common German labels. It scored 3.8%. Its labels were then extended after looking at the corpus layout ("Bruttosumme", "Steuerbetrag", "Nr. … vom", "Name:" under a party heading) and tried in priority order. That makes the baseline somewhat tuned to this corpus, which favours the baseline, not the LLM.
- **`llm:gpt-5-nano`** runs the application's own extraction (prompt `extract_invoice.v1`, reasoning effort `minimal`, the same post-processing and grading) through the budgeted LLM client. One live run on 11 Oct 2026 (`evals/reports/2026-10-11-llm-gpt-5-nano.md`); latency p50 4.7 s, p95 6.0 s per document.
  - Per field: currency 99%, VAT ID 96%, IBAN 94%, payable amount 92%, invoice number 90%, seller name 87%, buyer name 86%, gross total 84%, net total 82%, issue date 78%, tax total 76%, due date 38%.
  - What holds the critical score down: issue dates left empty (19% abstention) and gross totals (12.5% abstention). In every one of these cases the model itself answered null (19 dates, 12 totals); the grading dropped none. Reasoning effort `minimal` and `gpt-5-nano` are the cheapest settings; a stronger model or effort is the obvious next experiment.
  - **Hallucination needs reading with the truth's limits.** The truth is the embedded XML, and a PDF can print what its XML leaves out. IBAN shows 24.6% (17 of 69 documents without an IBAN in the XML), but 16 of those 17 IBANs are printed in the PDF; the 17th is the model returning the text "null". VAT ID shows 100%, which is 2 of 2 documents, and both IDs are printed.
  - One document (`zugferd_2p1_EN16931_Einfach_negativePaymentDue`) counts as abstaining on every field: the model wrote a NUL character into quoted evidence, PostgreSQL refused to cache the answer and the call failed. The client now removes NUL from answers (`docs/DECISIONS.md`); the run was not repeated.

## Validation parity

- **Reference set:**
  - every XML of the XRechnung test suite release that matches the configuration (`v2026-01-31`);
  - every corpus XML in `XML-Rechnung/`;
  - the embedded XML of every corpus PDF in `ZUGFeRDv2/correct` and `ZUGFeRDv2/fail`.
- **Reference verdicts:** the official KoSIT validator v1.6.0 (the version the configuration release names), run in Docker (`eclipse-temurin:21-jre`) with the full configuration release. Both zips are pinned by SHA-256.
- **Rule levels:** KoSIT prints every message with the rule's original level and applies the scenario's `customLevel` entries when it assesses a file. The comparison applies the same levels to decide which rules were fatal.

| Files compared | Excluded | Verdict agreement | Rule-set agreement |
|---------------:|---------:|------------------:|-------------------:|
| 176 | 64 | 100% | 100% |

Excluded files:
- 49 match no KoSIT scenario;
- 14 are not applicable for Eingang (MINIMUM, BASIC WL, legacy ZUGFeRD 1, XRechnung 2.x);
- 1 is not an invoice Eingang accepts.

## Limits

- The corpus consists of sample invoices made by software vendors. They are cleaner than real-world scans and photos, and most share one vendor's layout. Accuracy on real supplier invoices will be lower.
- PDF text order can differ from the visual order, which hurts any text-based reader; the LLM sees the same text the baseline sees.
- 104 documents give wide confidence intervals: the 95% interval of the baseline spans about 15 points.
- English and French are 10 documents together, too few to report per language.

## Rerunning

From `backend/`, with the corpus downloaded (`uv run poe fetch-corpus`):

```sh
uv run poe eval-dataset    # rebuild the dataset and its cache (free, deterministic)
uv run poe eval-baseline   # regex baseline (free)
uv run poe eval-parity     # needs Docker; downloads the pinned KoSIT validator and configuration
uv run poe eval-llm        # PAID: prints the estimate and asks for "yes"
```

`eval-llm` stops before `EVAL_BUDGET_USD` (default $0.75) would be exceeded, and allows at most two live runs per prompt version (`evals/data/llm-runs.json`). Cached answers make a rerun of finished documents free.
