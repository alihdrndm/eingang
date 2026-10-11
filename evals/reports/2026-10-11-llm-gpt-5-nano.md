# Extraction accuracy: `llm:gpt-5-nano`

Run of 2026-10-11 11:04 UTC on the dataset `zugferd-corpus-hybrid-cii` (104 documents, corpus commit `d891458e9822`); 104 documents scored.
System: model `gpt-5-nano`, prompt `extract_invoice.v1`.

## Headline

| System | Critical fields correct (95% CI) | Cost | Per document | Latency p50 | Latency p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `llm:gpt-5-nano` | 65.4% (56.7% to 74.0%) | $0.0334 | $0.000321 | 4,650 ms | 5,973 ms |
| `regex-baseline` | 21.2% (13.5% to 28.8%) | $0.0000 | $0.000000 | 3 ms | 6 ms |

A document's critical fields are correct when its invoice number, issue date, gross total and (if the invoice has one) payee IBAN are all correct. The interval is a 95% bootstrap confidence interval.

## Per field

| Field | Accuracy | Hallucination | Abstention |
| --- | ---: | ---: | ---: |
| `invoice_number` | 90.4% | n/a | 1.9% |
| `issue_date` | 77.9% | n/a | 19.2% |
| `due_date` | 38.2% | 11.4% | 52.9% |
| `currency` | 99.0% | n/a | 1.0% |
| `seller.name` | 86.5% | n/a | 1.0% |
| `seller.vat_id` | 96.1% | 100.0% | 2.0% |
| `payee_iban` | 94.3% | 24.6% | 0.0% |
| `buyer.name` | 85.6% | n/a | 2.9% |
| `net_total` | 81.7% | n/a | 16.3% |
| `tax_total` | 76.0% | n/a | 22.1% |
| `gross_total` | 83.7% | n/a | 12.5% |
| `payable_amount` | 92.3% | n/a | 1.0% |

Accuracy counts the documents whose truth has the field; hallucination, a value read where the truth has none; abstention, no value where the truth has one. `n/a`: the rate has no documents to count.

## Validation parity

| Validation parity |  |
| --- | ---: |
| Files compared | 176 |
| Excluded | 64 |
| Verdict agreement | 100.0% |
| Fatal rule set agreement | 100.0% |

## Method

Dataset, scoring rules, statistics and limits: [docs/EVALS.md](../../docs/EVALS.md).
