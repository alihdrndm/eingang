// pnpm dev: starts the backing services in Docker, waits until they are healthy, applies the
// migrations and creates the Temporal schedules (both idempotent, so a migrated database is
// left as it is), then runs the API, the Temporal worker and the frontend dev server on the
// host with prefixed output. Ctrl+C stops the three host processes; the containers keep running.
// Seed data is not loaded here: run `pnpm seed` once.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import concurrently from "concurrently";

const rootDir = fileURLToPath(new URL("../", import.meta.url));
const backendDir = fileURLToPath(new URL("../backend/", import.meta.url));

console.log("=== Starting db and temporal (docker compose) ===");
// docker is a real executable on every platform, so no shell is needed here.
const compose = spawnSync("docker", ["compose", "up", "-d", "--wait", "db", "temporal"], {
  cwd: rootDir,
  stdio: "inherit",
});
if (compose.error) {
  console.error(`dev: could not start docker: ${compose.error.message}`);
  process.exit(1);
}
if (compose.status !== 0) {
  console.error("dev: docker compose could not start db and temporal.");
  process.exit(compose.status ?? 1);
}

/** spawnSync that also runs .cmd shims on Windows, which needs the shell and one command line. */
function spawnCommand(command, args, options) {
  if (process.platform !== "win32") return spawnSync(command, args, options);
  return spawnSync([command, ...args].join(" "), { ...options, shell: true });
}

// The same setup steps as the e2e-setup service in compose.yaml, without the seed data.
for (const args of [["migrate", "--noinput"], ["ensure_schedules"]]) {
  console.log(`=== manage.py ${args.join(" ")} ===`);
  const result = spawnCommand("uv", ["run", "python", "manage.py", ...args], {
    cwd: backendDir,
    stdio: "inherit",
  });
  if (result.error) {
    console.error(`dev: could not start "uv": ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    console.error(`dev: "manage.py ${args[0]}" failed.`);
    process.exit(result.status ?? 1);
  }
}

let interrupted = false;
process.on("SIGINT", () => {
  interrupted = true;
});

console.log("=== Starting api, worker and web ===");
const { result } = concurrently(
  [
    { name: "api", command: "uv run poe api", cwd: backendDir, prefixColor: "blue" },
    { name: "worker", command: "uv run poe worker", cwd: backendDir, prefixColor: "magenta" },
    { name: "web", command: "pnpm --filter frontend dev", cwd: rootDir, prefixColor: "green" },
  ],
  { prefix: "name" },
);

result.then(
  () => process.exit(0),
  () => process.exit(interrupted ? 130 : 1),
);
