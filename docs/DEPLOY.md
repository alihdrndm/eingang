# Deploying Eingang

This guide deploys the public demo: the Vue app on Vercel, the API, the Temporal worker and a Temporal server on Railway, and the database and file storage on Supabase. Follow the numbered steps in order. Each step says what you should see when it worked.

Costs are in [`docs/COSTS.md`](COSTS.md). This guide contains no prices.

## Overview

```
Browser ──► Vercel (Vue SPA; rewrites /api/* to the API)
                 │
                 ▼
        Railway: api (Django + DRF, gunicorn) ──── starts workflows, sends signals ───┐
                 │                                                                     ▼
                 │                                        Railway: temporal (dev server, SQLite on a volume)
                 │                                                                     ▲
                 │                                              polls task queue "eingang-main"
                 │                                                                     │
                 │                                        Railway: worker (Temporal worker + activities)
                 │                                                                     │
                 ├──────────────► Supabase Postgres ◄──────────────────────────────────┤
                 └──────────────► Supabase Storage (S3 API) ◄──────────────────────────┤
                                                                                       └──► OpenAI API (only when LLM_ENABLED=true)
```

| Piece | Where | Built from | Public |
|-------|-------|------------|--------|
| Frontend | Vercel project, root directory `frontend` | `frontend/` with `frontend/vercel.json` | Yes, `https://<project>.vercel.app` |
| API | Railway service `api` | `backend/docker/api.Dockerfile`, build context = repository root | Yes, `https://<name>.up.railway.app` |
| Worker | Railway service `worker` | `backend/docker/worker.Dockerfile`, build context = repository root | No |
| Temporal | Railway service `temporal` | Docker image `temporalio/temporal:1.9.1` (the tag pinned in `compose.yaml`) | No. The dev server has no authentication; it is reached only over Railway's private network at `temporal.railway.internal:7233`. |
| Database | Supabase project `eingang`, region Frankfurt (`eu-central-1`) | — | Reached with the Session pooler connection string |
| Files | Supabase Storage, private bucket `documents`, through its S3-compatible endpoint | — | No. Keys stay on the server. |

The browser only talks to the Vercel origin. Vercel forwards every `/api/...` request to the Railway API, so the session and CSRF cookies are first-party. `/healthz`, `/readyz` and `/docs` are not under `/api`, so you open them on the Railway domain directly.

All three Railway services use the region `europe-west4`, close to Supabase in Frankfurt.

## Accounts and keys

You need:

| Account | Plan | What you take from it |
|---------|------|-----------------------|
| [Railway](https://railway.com) | Hobby | Three services, one volume, a public domain for `api` |
| [Vercel](https://vercel.com) | Hobby | One project for the frontend |
| [Supabase](https://supabase.com) | Free | One project: Postgres connection string, Storage bucket, S3 access keys |
| [OpenAI](https://platform.openai.com) | Prepaid credit | Optional. A separate API key for this project, with **auto-recharge off**. See [OpenAI (LLM features)](#openai-llm-features). |
| GitHub | — | The repository `alihdrndm/eingang`, connected to Railway and Vercel |

Keep every key and password in the services' variable settings only. Never commit them.

## Step 0: cap the Railway bill

Do this before anything else.

1. In Railway, open your workspace → **Usage**.
2. Set the usage **hard limit** to the lowest value Railway allows. When this guide was written that was USD 10 per month (`docs/ASSUMPTIONS.md`, H2); check the page for the current minimum. When the limit is reached, Railway stops the services instead of charging more.
3. Add a usage **email alert at USD 4**. When it fires, follow [Pausing the demo](#pausing-the-demo).
4. In OpenAI (if you use it), check that auto-recharge is off (Billing).

**You should see:** the hard limit and the alert listed on the Usage page.

## Supabase

1. Create a project: **New project**, name `eingang`, region **Central EU (Frankfurt)** (`eu-central-1`). Generate a database password and store it in your password manager.
   **You should see:** the project dashboard after a minute or two.
2. Copy the connection string: **Connect** (top bar) → **Session pooler** → **URI**. It looks like `postgresql://postgres.<project_ref>:[YOUR-PASSWORD]@<pooler-host>:5432/postgres`. Replace `[YOUR-PASSWORD]` with the database password. If the password contains characters such as `@`, `:`, `/`, `?` or `#`, percent-encode them (for example `@` becomes `%40`); the application decodes them. This is `DATABASE_URL`.
   Use the Session pooler, not the direct connection: it works over IPv4.
3. Encrypted connections: the application does not read query parameters from `DATABASE_URL`, so set `PGSSLMODE=require` on api and worker (the PostgreSQL client library reads it directly; see the variable table). Also turn on **Project Settings → Database → SSL Configuration → Enforce SSL on incoming connections**, so the server refuses unencrypted connections too.
4. Create the storage bucket: **Storage → New bucket**, name `documents`, **Public bucket off**.
   **You should see:** `documents` in the bucket list, without a "Public" label.
5. Create S3 credentials: **Project Settings → Storage → S3 Connection**. Copy the **Endpoint** (`https://<project_ref>.storage.supabase.co/storage/v1/s3`) and the **Region**. Under **S3 Access Keys**, choose **New access key**, and copy the access key ID and the secret access key. The secret is shown only once.

You now have the values for `DATABASE_URL`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`.

## Railway

Create the services in this order: `temporal`, `worker`, `api`. The API's pre-deploy command creates the Temporal schedules, so Temporal must be running first.

### 1. Project and the `temporal` service

1. **New Project → Empty project**. Name it `eingang`.
2. **Create → Docker Image**, image `temporalio/temporal:1.9.1`. Rename the service to `temporal` (the private host name `temporal.railway.internal` comes from the service name).
3. **Settings → Deploy → Custom Start Command**:

   ```
   temporal server start-dev --ip :: --port 7233 --headless --db-filename /data/temporal.db --namespace eingang
   ```

   Railway's start command replaces the image's entry point (`temporal`), so the command names it. Without it the deploy fails with "The executable `server` could not be found".
4. **Settings → Deploy → Restart Policy**: On Failure.
5. Add a volume: right-click the service (or the command palette) → **Attach volume**, mount path `/data`.
6. **Variables**: add `RAILWAY_RUN_UID` = `0`. The image runs as a non-root user and Railway volumes belong to root; without this the server cannot write `/data/temporal.db` (the same reason `compose.yaml` runs it as root; see `docs/DECISIONS.md`).
7. Do **not** generate a public domain.
8. Deploy.
9. **Settings → Deploy → Region**: `europe-west4` (shown as "EU West (Amsterdam)"). Railway offers the region selector only after a service's first deployment; changing it redeploys the service, and the still-empty volume moves with it.

**You should see:** the deployment is Active and stays running, and its log has no "unable to open database file" error. If the log says the server cannot listen on `::`, change `--ip ::` to `--ip 0.0.0.0`, redeploy, and record it in `docs/DECISIONS.md`.

### 2. The `worker` service

1. **Create → GitHub Repo**, choose `alihdrndm/eingang`. Rename the service to `worker`.
2. **Settings → Source**: leave **Root Directory** empty. Both Dockerfiles use the repository root as build context (the worker copies `backend/`; the API also copies `samples/` and `evals/latest.json`).
3. **Variables**: add `RAILWAY_DOCKERFILE_PATH` = `backend/docker/worker.Dockerfile`, then every variable in [Environment variables](#environment-variables) marked for the worker.
4. **Settings → Deploy**: Restart Policy On Failure; no healthcheck path; no start command (the image runs `python -m eingang.worker`). After the first deployment, set **Region** to `europe-west4`, as for `temporal`.
5. Do **not** generate a public domain.

**You should see:** the build log builds `backend/docker/worker.Dockerfile`; the deploy log shows `Stylesheets compiled in … s` and the deployment stays running. "Invalid configuration:" followed by a list means a variable is missing or wrong; every problem is listed at once.

### 3. The `api` service

1. **Create → GitHub Repo**, the same repository. Rename the service to `api`.
2. **Settings → Source**: leave **Root Directory** empty.
3. **Variables**: add `RAILWAY_DOCKERFILE_PATH` = `backend/docker/api.Dockerfile`, `PORT` = `8010`, then every variable in [Environment variables](#environment-variables) marked for the API.
4. **Settings → Deploy**:
   - **Pre-deploy Command**:

     ```
     sh -c "python manage.py migrate --noinput && python manage.py seed_rules && python manage.py ensure_schedules"
     ```

     It applies the migrations, loads the curated rule explanations and creates or updates the Temporal schedules. All three are idempotent and make no LLM call. The command is wrapped in `sh -c` because `&&` needs a shell. If it fails, the new deployment does not go live and the previous one keeps running.
   - **Custom Start Command**: leave empty. The image runs `gunicorn eingang.wsgi --workers 1 --threads 4 --bind [::]:$PORT`.
   - **Healthcheck Path**: `/healthz`.
   - **Restart Policy**: On Failure. After the first deployment, set **Region** to `europe-west4`, as for `temporal`.
5. **Settings → Networking → Generate Domain**, target port `8010`. Note the host name, for example `eingang-api-production.up.railway.app`. This is `RAILWAY_API_HOST`.
6. Set `ALLOWED_HOSTS` to `<that host name>,healthcheck.railway.app`. Railway's deploy healthcheck sends `Host: healthcheck.railway.app`; without it the healthcheck gets `400` and the deploy fails.

**You should see:** the pre-deploy log ends with `Seeded … curated rule explanations; …` and `Schedule 'daily-maintenance' is up to date.`, then the healthcheck passes and the deployment is **Active**. `https://<RAILWAY_API_HOST>/healthz` returns `{"status":"ok"}`.

### Environment variables

Set these on the services named in the "Service" column. `api` and `worker` load the same settings class at start-up, and it refuses to start when a required value is missing, so the simplest way is:

1. Put the four secrets (`DJANGO_SECRET_KEY`, `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`) once into the project's **Settings → Shared Variables**.
2. In each of `api` and `worker`, paste the same block (**Variables → Raw Editor**) with the other values and a reference for each secret, for example `DATABASE_URL="${{shared.DATABASE_URL}}"`.
3. Add the service-specific ones. "Default" is the value in `backend/src/eingang/config.py`; leave a variable unset to use it.

**Railway-only**

| Variable | Service | Value |
|----------|---------|-------|
| `RAILWAY_DOCKERFILE_PATH` | api | `backend/docker/api.Dockerfile` |
| `RAILWAY_DOCKERFILE_PATH` | worker | `backend/docker/worker.Dockerfile` |
| `PORT` | api | `8010` (the port gunicorn binds and the domain targets) |
| `RAILWAY_RUN_UID` | temporal | `0` |

**Application settings** (every setting in `config.py`)

| Variable | Service | Required in production | Default | Production value / what it does |
|----------|---------|------------------------|---------|---------------------------------|
| `DJANGO_SECRET_KEY` | api, worker | Yes | development value | A long random string, the same on both. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. The app refuses to start with the development value when `DJANGO_DEBUG=false`. |
| `DJANGO_DEBUG` | api, worker | Yes | `true` | `false`. Also makes the session and CSRF cookies `Secure`. |
| `ALLOWED_HOSTS` | api, worker | Yes | `localhost,127.0.0.1` | `<RAILWAY_API_HOST>,healthcheck.railway.app`. Comma list of host names the API answers to. |
| `FRONTEND_ORIGIN` | api, worker | Yes | `http://localhost:3110` | `https://<project>.vercel.app` (no trailing slash). Added to the CSRF trusted origins. Set it after the Vercel step. |
| `DATABASE_URL` | api, worker | Yes | local Compose database | The Supabase Session pooler URI from the Supabase step. |
| `PGSSLMODE` | api, worker | Yes in production | (unset) | `require`: the database connection is always encrypted. |
| `STORAGE_BACKEND` | api, worker | Yes | `local` | `s3`. `local` writes to the container's disk, which is lost on every deploy and is not shared between api and worker. |
| `S3_ENDPOINT_URL` | api, worker | With `s3` | empty | `https://<project_ref>.storage.supabase.co/storage/v1/s3` |
| `S3_REGION` | api, worker | No | empty | The region shown on Supabase's S3 Connection page. |
| `S3_ACCESS_KEY_ID` | api, worker | With `s3` | empty | From Supabase's S3 Access Keys. |
| `S3_SECRET_ACCESS_KEY` | api, worker | With `s3` | empty | From Supabase's S3 Access Keys. |
| `S3_BUCKET` | api, worker | No | `documents` | The private bucket. |
| `TRUSTED_PROXY_HOPS` | api | Yes, after measuring | `0` | Number of proxy hops whose `X-Forwarded-For` entries the rate limits trust. Measure it as described in [TRUSTED_PROXY_HOPS](#trusted_proxy_hops). |
| `PROXY_SHARED_SECRET` | api (and Vercel) | Yes | empty | A long random string, the same on `api` and in Vercel. The Vercel middleware sends it with the visitor's address so the rate limits can key on the visitor; see [Rate limits through Vercel](#rate-limits-through-vercel). Empty: the forwarded address is ignored. |
| `PROXY_SECRET_REQUIRED` | api | Yes on the public demo | `false` | `true`: `/api` answers only requests that carry `PROXY_SHARED_SECRET` (`403 PROXY_REQUIRED` otherwise), so a copy of the frontend deployed elsewhere cannot use this API. `/healthz`, `/readyz` and `/docs` stay open. Set it to `false` while measuring `TRUSTED_PROXY_HOPS`. |
| `TEMPORAL_ADDRESS` | api, worker | Yes | `localhost:7233` | `temporal.railway.internal:7233` |
| `TEMPORAL_NAMESPACE` | api, worker | No | `eingang` | `eingang` |
| `MAX_UPLOAD_BYTES` | api, worker | No | `4194304` | Largest accepted file, in bytes. Vercel limits a proxied request body (`docs/ASSUMPTIONS.md`, E2), so do not raise it. |
| `SANDBOX_TTL_HOURS` | api, worker | No | `24` | Hours until a sandbox and its files are deleted by the daily maintenance. |
| `SANDBOX_DAILY_LIMIT` | api | No | `50` | Sandboxes created per 24 hours, all visitors together. |
| `SANDBOX_MAX_UPLOADS` | api | No | `10` | Uploaded files per sandbox. |
| `SANDBOX_STORAGE_BUDGET_BYTES` | api | No | `314572800` | No sandbox upload is accepted while all sandboxes' files together exceed this. |
| `COMPARE_HYBRID_PDF` | worker | No | `true` | PDF-versus-XML comparison for hybrid PDFs (needs the LLM). |
| `SEED_PASSWORD` | — | No | `eingang-dev` | Password of the local seed users. Not used in production; leave unset. |
| `MAILBOX_ENABLED` | api, worker | No | `false` | Optional IMAP intake; off in the public demo. See [Mailbox intake](#mailbox-intake-optional). |
| `MAILBOX_HOST`, `MAILBOX_PORT`, `MAILBOX_USER`, `MAILBOX_PASSWORD`, `MAILBOX_ORG_SLUG` | api, worker | With `MAILBOX_ENABLED=true` | empty, `993` | See [Mailbox intake](#mailbox-intake-optional). |
| `LLM_ENABLED` | api, worker | No | `false` | Master switch for the LLM features. See [OpenAI](#openai-llm-features). |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_PRICE_INPUT_PER_MTOK`, `OPENAI_PRICE_CACHED_INPUT_PER_MTOK`, `OPENAI_PRICE_OUTPUT_PER_MTOK`, `LLM_SPENT_ELSEWHERE_USD` | api, worker | With `LLM_ENABLED=true` | empty | See [OpenAI](#openai-llm-features). |
| `OPENAI_REASONING_EFFORT` | api, worker | No | empty | See [OpenAI](#openai-llm-features). |
| `LLM_BUDGET_USD_LIFETIME` | api, worker | No | `2.00` | Lifetime LLM budget in USD. |
| `LLM_BUDGET_USD_MONTHLY` | api, worker | No | `1.50` | Monthly LLM budget in USD. |
| `LLM_BUDGET_USD_DAILY_PUBLIC` | api, worker | No | `0.10` | Daily budget for calls made for sandboxes. |
| `LLM_MAX_CALLS_PER_SANDBOX` | api, worker | No | `3` | LLM calls per sandbox. |
| `EVAL_BUDGET_USD` | — | No | `0.75` | Used only by the local evaluation run. Leave unset. |

## Vercel

1. Put the API host into the rewrite. In `frontend/vercel.json`, the `/api/:path*` destination points at this repository's own deployment; replace that host with the host name from your Railway `api` step (host name only: no `https://`, no trailing slash), so the line reads, for example:

   ```json
   "destination": "https://eingang-api-production.up.railway.app/api/:path*"
   ```

   Vercel does not substitute environment variables in `vercel.json`, so the host is written literally. Commit and push the change.
2. In Vercel: **Add New → Project**, import `alihdrndm/eingang`.
3. **Root Directory**: `frontend`. Leave **Include files outside the root directory in the Build Step** on: the pnpm workspace's lock file and `package.json` (with the pinned pnpm version) are at the repository root.
4. **Framework Preset**: Vite. Build command and output directory come from `frontend/vercel.json` (`pnpm run build`, `dist`); leave the install command on its default. Vercel runs `pnpm install` in `frontend/`, and pnpm installs the whole workspace from the root lock file.
5. **Environment Variables**: add `ENABLE_EXPERIMENTAL_COREPACK` = `1`. Vercel then uses the pnpm version pinned in the root `package.json` (`packageManager`) instead of picking one from the lock file. Add `PROXY_SHARED_SECRET` as well ([Rate limits through Vercel](#rate-limits-through-vercel)).
6. **Settings → Build and Deployment → Node.js Version**: 24.x (the version in `.nvmrc`).
7. Deploy. Note the production domain, for example `https://eingang.vercel.app`.
8. In Railway, set `FRONTEND_ORIGIN` on `api` and `worker` to that origin. Railway redeploys both services.

**You should see:** the Vercel build log runs `vue-tsc --noEmit && vite build` and finishes; `https://<project>.vercel.app/` shows the homepage; `https://<project>.vercel.app/api/v1/accuracy` returns JSON from the API.

The response headers of the app's pages (not of `/api/...`, which come from the API) include `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and `X-Frame-Options: DENY`, set in `frontend/vercel.json`. The policy allows only the app's own origin, plus inline styles, `data:` fonts and images, and `blob:` workers and images for the PDF viewer. If you add anything that loads from another origin, the policy must change with it.

## First deploy checklist

1. The `api` deployment is Active and its pre-deploy log shows the migrations, `Seeded … curated rule explanations` and `Schedule 'daily-maintenance' is up to date.` There is no other first-run step.
2. `https://<RAILWAY_API_HOST>/healthz` → `200 {"status":"ok"}`.
3. `https://<RAILWAY_API_HOST>/readyz` → `200 {"status":"ok","checks":{"database":"ok","temporal":"ok"}}`. A `503` names the unreachable dependency in `checks`.
4. Open `https://<project>.vercel.app/`, choose **Open the sandbox**. The inbox shows twelve sample documents.
5. Upload `backend/tests/fixtures/invoices/invoice.ubl.xml` into the sandbox. (The twelve sample files are already in every sandbox, so uploading one of them is reported as a duplicate.) It moves through the processing steps and leaves `processing` for a status such as `awaiting_approval` or `needs_review`; this proves the worker, Temporal, the database and Storage work together.
6. In Supabase **Storage → documents**, objects appear under `orgs/<org_id>/documents/…`.
7. Measure and set `TRUSTED_PROXY_HOPS` (next section).

## TRUSTED_PROXY_HOPS

Requests reach the API through Vercel and Railway's proxies, which add entries to `X-Forwarded-For`. The rate limits (per client IP) trust exactly `TRUSTED_PROXY_HOPS` entries from the right of that header. Too low, and every visitor shares one proxy address and one limit. Too high, and a visitor can choose their own key by sending the header. The right value can only be measured on the deployed stack.

The value is measured on the Railway domain (`https://<RAILWAY_API_HOST>`). Requests through Vercel cannot be limited per client with any value, so measuring there gives all `200` at every value; [Rate limits through Vercel](#rate-limits-through-vercel) covers that path.

Use the public endpoint `GET /api/v1/accuracy`, which has the general limit of 120 requests per minute per client IP:

1. Set `TRUSTED_PROXY_HOPS` on `api` to a candidate value, starting with `1`, and wait until the redeploy is Active.
2. **Baseline.** From your computer (Git Bash on Windows), send 150 requests in parallel (one after another they take longer than the one-minute window):

   ```sh
   seq 1 150 | xargs -P 16 -I{} curl -s -o /dev/null -w "%{http_code}\n" \
     https://<RAILWAY_API_HOST>/api/v1/accuracy | sort | uniq -c
   ```

   Some answers must be `429`. If all are `200`, the counted entry changes on every request: increase the value by one and repeat from step 1.
3. **Spoofing test.** Wait a minute, then run the same command with a different fake `X-Forwarded-For` on each request, by adding `-H "X-Forwarded-For: 198.51.100.{}"` after `curl`. Correct: some answers are `429`, because the fake header did not change the key. Too high: all `150` are `200`; decrease the value by one.
4. **Shared-key test.** Right after step 3, while your computer is still limited, open `https://<RAILWAY_API_HOST>/api/v1/accuracy` from a different network (for example a phone on mobile data, Wi-Fi off). Correct: `200`. Too low: `429`, because both clients share one proxy address.
5. If step 4 gave `429`, increase the value by one and repeat from step 1 (wait a minute between rounds). Use the smallest value for which steps 2 and 3 show `429`s and step 4 shows `200`.
6. Record the value and the date in `docs/DECISIONS.md` (entry "Proxy hops for rate limits") and here:

   Measured value: **2** (11 Oct 2026). Railway's edge appends an internal proxy address to `X-Forwarded-For`, so a request sent straight to the Railway domain arrives as `client, railway-proxy`. With 2 the key is the client, and a fake header lands further left and is ignored (150 spoofed requests: 120 × `200`, 30 × `429`). With 1 the key is the proxy address, which changes per request, so nothing is limited. The shared-key test from a second network was not run.

   Requests through Vercel were not limited at any value from 1 to 4 (150 requests, all `200`): on that path the counted entry changes per request. They are limited by the next section instead.

## Rate limits through Vercel

The Vercel rewrite reaches the API from a different Vercel address on every request, so no `TRUSTED_PROXY_HOPS` value finds the visitor on that path, and it is the path every browser uses. A Vercel middleware (`frontend/middleware.ts`, run for `/api/*` before the rewrite) therefore sends the visitor's address in `X-Eingang-Client-IP` together with `PROXY_SHARED_SECRET`. The API uses that address for the per-IP limits only when the secret matches; otherwise it falls back to `TRUSTED_PROXY_HOPS`. The middleware drops both headers if a visitor sends them.

1. Generate the secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Railway: put it into **Settings → Shared Variables** as `PROXY_SHARED_SECRET`, and add `PROXY_SHARED_SECRET="${{shared.PROXY_SHARED_SECRET}}"` to `api`. Railway redeploys `api`.
3. Vercel: project → **Settings → Environment Variables**, add `PROXY_SHARED_SECRET` with the same value for **Production**. Then **Deployments** → the latest production deployment → **Redeploy**, because a new variable only reaches new deployments.
4. Check, after both deploys are Active: send 150 requests through Vercel and some answers must be `429`:

   ```sh
   seq 1 150 | xargs -P 16 -I{} curl -s -o /dev/null -w "%{http_code}
"      https://<project>.vercel.app/api/v1/accuracy | sort | uniq -c
   ```

   Run it again with `-H "X-Forwarded-For: 198.51.100.{}" -H "X-Eingang-Client-IP: 198.51.100.{}"` after `curl`: still some `429`, because the middleware replaces the address and the API ignores a fake `X-Forwarded-For`.

5. On `api`, set `PROXY_SECRET_REQUIRED=true`. Then `curl -s https://<RAILWAY_API_HOST>/api/v1/accuracy` answers `403` with `"code":"PROXY_REQUIRED"`, while `https://<project>.vercel.app/api/v1/accuracy` still answers `200`. Without this, anyone can deploy the frontend with a rewrite to your API host, and their requests reach the API from changing Vercel addresses, past the per-IP limits.

**You should see:** `429`s in both runs. All `200` means the secret differs between Vercel and `api`, or the Vercel deployment predates the variable (a redeploy takes about a minute to go live after it shows Ready).

   Measured (12 Oct 2026): 120 × `200` and 30 × `429` in both runs.

## Pausing the demo

Pausing stops all compute on Railway. The data stays in Supabase, and Temporal's state stays on its volume. The Vercel site stays up; while the services are stopped, opening the sandbox shows an error.

**Pause:** for `api`, then `worker`, then `temporal`: open the service → **Deployments** → the active deployment's menu (⋮) → **Remove**.

**You should see:** no active deployment on any of the three services.

**Resume:** for `temporal`, then `worker`, then `api` (the API's pre-deploy command needs Temporal): open the service → **Deployments** → the most recent deployment's menu → **Redeploy**. Then run the [first deploy checklist](#first-deploy-checklist) steps 2–4.

While the services are paused, the daily maintenance does not run, so it does not touch the database. Supabase Free pauses a project after a week of inactivity. If that happens, open the project in the Supabase dashboard and choose **Restore project**; restoring is free (`docs/ASSUMPTIONS.md`, H1). The same applies if Supabase pauses the project while the services run.

## Reading logs

- **Railway:** open a service → **Deployments** → **View logs** (build, pre-deploy and deploy logs per deployment). Production logs are JSON, one line per request with method, path without the query string, status, duration and request id. They contain no request bodies, names, email addresses or IBANs.
- **Vercel:** project → **Logs** (requests, including the `/api` rewrites) and **Deployments** → a deployment → **Build Logs**.
- **Temporal:** the Web UI is not exposed. Workflow failures appear in the worker's log.

## Updating

Push to `main`. Railway rebuilds and redeploys `api` and `worker` from the new commit, and Vercel rebuilds the frontend. On every API deploy the pre-deploy command applies new migrations and updates the rule explanations and schedules before the new version goes live; if it fails, the old version keeps running.

To move to a new Temporal image, change the tag in `compose.yaml`, test locally, record it in `docs/DECISIONS.md`, then change the image on the `temporal` service (**Settings → Source**). The volume keeps its data. Railway stops the old deployment before the new one mounts the volume, so Temporal is unavailable for a short time; uploads made then are stored and started later by the daily maintenance.

## Rolling back

- **Railway:** open the service → **Deployments** → an earlier deployment's menu → **Rollback**. Roll back `api` and `worker` to the same commit.
- **Vercel:** project → **Deployments** → an earlier production deployment → **Promote** (Instant Rollback).

A rollback does not undo database migrations. Before rolling back past a commit that added a migration, check that the older code works with the newer schema; if it does not, fix forward instead.

## Mailbox intake (optional)

Eingang can read invoices from a mailbox. Every five minutes the schedule `mailbox-poll` runs `MailboxPollWorkflow`: it connects over IMAP (TLS), reads the unseen messages in `INBOX`, stores each PDF or XML attachment as a document of one organisation (`source = email`, with the sender's address), marks the message as read, and starts processing each new document. Attachments get the same checks as uploads: at most `MAX_UPLOAD_BYTES`, the type is decided by content (PDF, or a UBL/CII invoice XML without a DOCTYPE), and a file the organisation already has is skipped. Other attachments, and messages without a usable attachment, are marked as read and otherwise ignored.

The intake is **disabled in the public demo** (`MAILBOX_ENABLED=false`).

### Setting it up with Gmail

Use a mailbox that receives only invoices, because every unseen message in its inbox is read.

1. In the Google account of that mailbox, turn on **2-Step Verification** (Google Account → Security → 2-Step Verification). App passwords are only available with it.
2. Create an **app password** (Google Account → Security → 2-Step Verification → App passwords), for example named `eingang`. Google shows a 16-character password once; copy it. Your normal Google password does not work over IMAP.
3. Make sure IMAP access is on in Gmail (Settings → See all settings → Forwarding and POP/IMAP → IMAP access).
4. Set these variables for the **worker** (and for the process that runs `ensure_schedules`):

   | Variable | Value |
   |----------|-------|
   | `MAILBOX_ENABLED` | `true` |
   | `MAILBOX_HOST` | `imap.gmail.com` |
   | `MAILBOX_PORT` | `993` |
   | `MAILBOX_USER` | the full Gmail address |
   | `MAILBOX_PASSWORD` | the app password from step 2 (without spaces) |
   | `MAILBOX_ORG_SLUG` | the slug of the organisation that receives the documents |

   With `MAILBOX_ENABLED=true` the configuration check refuses to start until all of them are set.
5. Restart the worker, then create the schedule:

   ```sh
   cd backend
   uv run poe ensure-schedules
   ```

   It prints the schedules that now exist; `mailbox-poll` must be among them. Running it again is safe. To turn the intake off, set `MAILBOX_ENABLED=false` and run `ensure_schedules` again: it removes the `mailbox-poll` schedule.

   On Railway, `ensure_schedules` is part of the `api` service's pre-deploy command, so set the variables on `api` as well as `worker`; saving them redeploys `api`, which creates or removes the schedule.

To check the setup without waiting five minutes, trigger the schedule `mailbox-poll` in the Temporal Web UI. A wrong address or app password makes the run fail at once with "The mailbox refused MAILBOX_USER/MAILBOX_PASSWORD."; an unknown slug fails with a message naming `MAILBOX_ORG_SLUG`.

## OpenAI (LLM features)

Eingang uses the LLM for three things: reading plain-PDF invoices, comparing a hybrid PDF's visible text with its XML, and explaining validation rules once each. Everything works without it (`LLM_ENABLED=false`); the UI then says why a result is missing.

The spend is capped in the application, but the account itself should be a hard stop too:

1. Create a **separate API key** for this project (OpenAI dashboard → API keys).
2. Keep **auto-recharge off** (Billing → auto-recharge). The prepaid credit is then the final limit, whatever happens in the application.
3. Set these variables for the **api** and the **worker**:

   | Variable | Value |
   |----------|-------|
   | `LLM_ENABLED` | `true` |
   | `OPENAI_API_KEY` | the project's key |
   | `OPENAI_MODEL` | the confirmed model (see `docs/DECISIONS.md`) |
   | `OPENAI_REASONING_EFFORT` | the lowest effort the model accepts, or empty for a non-reasoning model |
   | `OPENAI_PRICE_INPUT_PER_MTOK`, `OPENAI_PRICE_CACHED_INPUT_PER_MTOK`, `OPENAI_PRICE_OUTPUT_PER_MTOK` | the model's prices in USD per million tokens, from the official pricing page |
   | `LLM_SPENT_ELSEWHERE_USD` | the spend recorded in the project's other database: run `uv run poe llm-spend` there and copy the number |
   | `LLM_BUDGET_USD_LIFETIME`, `LLM_BUDGET_USD_MONTHLY`, `LLM_BUDGET_USD_DAILY_PUBLIC`, `LLM_MAX_CALLS_PER_SANDBOX` | the budgets; defaults 2.00, 1.50, 0.10 and 3 |

   With `LLM_ENABLED=true` the configuration check refuses to start until the key, the model, the three prices and `LLM_SPENT_ELSEWHERE_USD` are set.

Every call, refused or not, is one row in `llm_calls` with its cost; nothing in the application deletes it.
