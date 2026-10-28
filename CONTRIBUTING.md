# Contributing to Eingang

## Development setup

Prerequisites: Docker with Compose v2, Node 24 (see `.nvmrc`) with Corepack, and [uv](https://docs.astral.sh/uv/). Python 3.13 is installed by uv.

```sh
corepack enable
pnpm install
cd backend && uv sync --extra worker && cd ..
pnpm dev
```

`pnpm dev` starts PostgreSQL and Temporal in Docker, applies the migrations and creates the Temporal schedules (both do nothing when already up to date), then runs the API (http://localhost:8010), the Temporal worker and the web app (http://localhost:3110) with hot reload. The Temporal UI is at http://localhost:8233. Ctrl+C stops the three host processes; the containers keep running.

The database starts empty. To load the seed data (the local organisation with one user per role and the twelve samples), run `pnpm seed` once while `pnpm dev` is running, or `pnpm db:reset` to start over.

Copy `backend/.env.example` to `backend/.env` only if you need to change a default. Never commit `.env`.

### Root scripts

Run them from the repository root. They work on Windows, macOS and Linux.

| Script | Does |
|--------|------|
| `pnpm dev` | Starts `db` and `temporal` in Docker, migrates and ensures the Temporal schedules, then runs the API, the worker and the frontend with hot reload. |
| `pnpm verify` | Backend lint, typecheck, the pinned ZUGFeRD corpus (downloaded on the first run, about 150 MB), tests with coverage and OpenAPI freshness; frontend lint, typecheck, tests, generated-types freshness and build. Stops at the first failure. |
| `pnpm test:e2e` | Stops the development containers, runs the whole stack as the separate Compose project `eingang-e2e`, runs the Playwright smoke tests against http://localhost:3110, and removes the stack. Stop `pnpm dev` first: the ports are the same. |
| `pnpm gen:api` | Exports the OpenAPI schema to `backend/openapi.json` and regenerates `frontend/src/api/schema.d.ts`. |
| `pnpm db:reset` | Drops and recreates the local database, migrates and loads the seed data. The LLM ledger and cache tables are kept. |
| `pnpm seed` | Loads the local seed data: the curated rule explanations and the local organisation "Holzwerk Brandt GmbH (local)" with one user per role and the twelve samples. |

Backend tasks run from `backend/` with `uv run poe <task>`: `lint`, `format`, `typecheck`, `test`, `openapi`, `build-samples`, `create-org`, and the others listed in `backend/pyproject.toml`. Frontend scripts run with `pnpm --filter frontend <script>`: `lint`, `typecheck`, `test`, `build`, `test:e2e`.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, `build:`, `ci:`), with a scope where it helps (`feat(invoices): …`). Keep commits small: one logical change each, so every commit builds and is easy to review.
- **Tests come with the code** they cover, in the same commit or the one right after. Backend tests live in `backend/tests/`, mirroring `backend/src/`; frontend unit tests sit next to the component as `*.test.ts`. Every rule, check, error code and status transition has a test whose name contains its ID.
- **Run `pnpm verify` before pushing.** CI runs the same checks plus the end-to-end smoke test.
- **Tests never call the public internet.** HTTP is mocked; LLM responses come from a fake SDK and from responses recorded once with `uv run poe llm-smoke` (`backend/tests/fixtures/recorded/`). CI runs with `LLM_ENABLED=false` and needs no secrets.
- **Python:** full type hints (mypy strict), Ruff for lint and format, `Decimal` for money, timezone-aware UTC datetimes, configuration only through `backend/src/eingang/config.py`. Never log request bodies, file contents, names, email addresses, IBANs, prompts or model output.
- **TypeScript:** strict; API types only from the generated `schema.d.ts`; colours, spacing and fonts only from `frontend/src/styles/tokens.css`; every data view has loading, empty, error and success states.
- **Errors** are `application/problem+json` with a `code` documented in `docs/ERRORS.md`. A new code needs an entry there.
- **Dependencies:** add one only when it is needed, and commit the updated `backend/uv.lock` or `pnpm-lock.yaml`.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make the change with tests, run `pnpm verify`, and push to your fork.
3. Open a pull request against `main` and fill in the template.

`main` is protected: every pull request needs the CI checks (`backend`, `frontend`, `e2e`) to pass, an approving review from the maintainer (`.github/CODEOWNERS`), and all review threads resolved. Pull requests are squash-merged, so the title becomes the commit message: write it as a Conventional Commit. CI for a first-time or outside contributor starts after the maintainer approves the run. CI never has access to secrets, so do not add steps that need one.

## Regenerating generated files

- **API types:** after changing an endpoint or serializer, run `pnpm gen:api` and commit `backend/openapi.json` and `frontend/src/api/schema.d.ts` together. CI fails if either is stale.
- **Sandbox samples:** the twelve samples in `samples/` are generated from `backend/tools/sample_data.py`. Run `cd backend && uv run poe build-samples` and commit `samples/`. It rewrites `samples/precomputed/S08.json` and `S10.json` without the precomputed LLM answers, so restore those two keys (`git checkout -p samples/precomputed/`) unless they must change. The S08 extraction and S10 comparison in `samples/precomputed/` come from `uv run poe precompute-samples`, which makes live LLM calls and costs money; run it only when those results must change.
- **Vendored rules:** `uv run poe fetch-rules` downloads the pinned KoSIT releases and checks their SHA-256 sums. Update `NOTICE` when a vendored artefact changes.

## Where decisions are recorded

| File | What |
|------|------|
| `docs/DECISIONS.md` | Dated decisions and every deviation from the specification, with the reason. |
| `docs/ASSUMPTIONS.md` | Everything assumed but not yet verified, with its impact. |
| `docs/ERRORS.md` | Every error code, its status and meaning. |
| `docs/ARCHITECTURE.md` | Component map, request flow, workflows, and where each rule is implemented. |
| `docs/EVALS.md` | How the evaluation works, how to rerun it, and its limits. |
| `docs/walkthrough/` | One walkthrough per milestone. |
| `NOTICE` | Every vendored or bundled third-party artefact and its licence. |

## Reporting security problems

Do not open a public issue. See [SECURITY.md](SECURITY.md).
