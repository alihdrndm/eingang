# Costs

Only numbers that were measured here or read from an official price page are in this file. Measurements: the e2e Compose stack (the production Dockerfiles) on the development machine, idle for 5 minutes after one sandbox with the twelve samples was opened; one `docker stats --no-stream` sample, taken on 10 Oct 2026.

## Measured idle use

| Service | Runs on | Memory | CPU |
|---------|---------|-------:|----:|
| `api` (gunicorn, 1 worker, 4 threads) | Railway | 102.1 MiB | 0.11 % |
| `worker` (Temporal worker, SaxonC loaded) | Railway | 286.9 MiB | 0.00 % |
| `temporal` (`temporalio/temporal`, start-dev) | Railway | 99.0 MiB | 4.11 % |
| `db` (PostgreSQL 17) | Supabase in production | 38.7 MiB | 14.96 % (local only) |
| `web` (vite preview) | e2e only; Vercel in production | 62.7 MiB | 0.00 % |

Storage after migrating and opening one sandbox: database **9,615,027 bytes (9.4 MB)**; stored files **972 KB** for one sandbox's twelve samples and their derived files.

CPU is the share of one core in that single sample; the Temporal figure includes its own polling and is a snapshot, not an average.

## Railway

Prices from https://railway.com/pricing, read on 10 Oct 2026: memory $0.00000386 per GB per second (about $10 per GB per month), CPU $0.00000772 per vCPU per second (about $20 per vCPU per month), egress $0.05 per GB, volumes $0.15 per GB per month. Hobby plan: $5 per month, including $5 of usage.

Estimate for this project, always on, from the measurements above (memory converted from MiB to GB):

| Service | Memory cost / month | CPU cost / month |
|---------|--------------------:|-----------------:|
| api | 0.0997 GB × $10 = $1.00 | 0.0011 vCPU × $20 = $0.02 |
| worker | 0.2802 GB × $10 = $2.80 | 0.0000 vCPU × $20 = $0.00 |
| temporal | 0.0967 GB × $10 = $0.97 | 0.0411 vCPU × $20 = $0.82 |
| **Total** | **$4.77** | **$0.84** |

About **$5.61 per month** of usage when all three services run all month. The Temporal volume (its SQLite file) is small; volume storage at $0.15 per GB is not measured separately. Egress is not measured.

The scheduled work (one maintenance run per day, up to ten sandbox seeds per day) was not measured separately and is not in the estimate.

**Stop rule (HANDOFF "Deployment"):** this estimate is above $4.50 per month on its own, before any other service the owner already runs on Railway. The default is to keep the three services **paused** except while the owner is using the deployed app (`docs/DEPLOY.md`, "Pausing the demo"). Other options: share one Temporal server with another project, or deploy only for demos.

**Measured on Railway:** on 11 Oct 2026, after the first deploy (all three services in EU West) and the deploy checks, Railway's usage page showed **$0.0199** for the `eingang` project in the billing cycle (memory most of it). The workspace has a compute hard limit of $10 ("All resources stopped at this amount") and an email alert at $5.

## Supabase

Free plan, from https://supabase.com/pricing (10 Oct 2026): 500 MB database per project, 1 GB file storage, 5 GB egress, projects paused after 1 week of inactivity, at most 2 active free projects.

Measured locally: 9.4 MB of database in total after one sandbox, and 972 KB of files per sandbox. Sandboxes are deleted after 24 hours by the daily maintenance, so the free limits are far away at demo use.

## Vercel

The frontend is a static build behind Vercel's rewrites; it runs on Vercel's free (Hobby) tier, deployed on 11 Oct 2026 at no cost.

## OpenAI

Total live spend recorded in the ledger so far: **$0.034000**, plus one evaluation call of about $0.0003 that the ledger lost (see below) (`uv run poe llm-spend` prints the current total).

| Run | Date | Calls | Tokens (input / cached / output) | Cost |
|-----|------|------:|----------------------------------|-----:|
| Smoke call (`uv run poe llm-smoke`), gpt-5-nano, reasoning effort minimal | 11 Oct 2026 | 3 | 3,527 / 0 / 990 | $0.000572 |
| Sample precompute (`uv run poe precompute-samples`): S08 extraction, S10 comparison | 11 Oct 2026 | 2 cache hits | 0 / 0 / 0 | $0.000000 |
| Evaluation run (`uv run poe eval-llm`), 104 documents | 11 Oct 2026 | 98 paid + 5 cache hits | 288,344 / 198,016 / 69,808 | $0.033428 |

The smoke call cost 39% of its estimate ($0.001485): the estimate assumes every output token up to `max_output_tokens` is used, and the answers used 739 of 2,500 (extract), 178 of 600 (compare) and 73 of 400 (explain). The budgets are in `docs/DEPLOY.md` and `HANDOFF.md`: $2.00 lifetime per project, $1.50 per month, $0.10 per day for public demo use, 3 calls per sandbox. Estimates shown before the paid steps (gpt-5-nano prices, $0.05 input / $0.005 cached / $0.40 output per million tokens):

| Step | Estimated maximum |
|------|------------------:|
| Smoke call (`uv run poe llm-smoke`, 3 calls) | $0.0015 |
| Sample precompute (`uv run poe precompute-samples`) | $0.0013 (≈ $0 from the cache after the smoke call) |
| Evaluation run (`uv run poe eval-llm`, 104 documents) | $0.1107 printed by the tool (about $0.04 at the smoke call's real usage); capped at $0.75 |

The evaluation run made 99 API calls but recorded 98: one answer contained a NUL character, PostgreSQL refused to cache it, and the rollback also removed that call's ledger row. Its cost is not known exactly; at the run's average ($0.000341 per paid call) it is about $0.0003. The client now removes NUL from answers and writes the cache in a savepoint, so a failed cache write keeps the ledger row. The run cost 30% of its estimate. Most of the gap is output: the estimate assumes all 2,500 output tokens per document ($0.1040), and the answers used about 712 on average ($0.0279). The input estimate was low (it counts the prompt and text, not the output schema: 134,455 estimated against 288,344 actual tokens), and caching (69% of input tokens, billed at a tenth) kept the input cost ($0.0055) below it.

This file is updated with the real spend after each paid step.
