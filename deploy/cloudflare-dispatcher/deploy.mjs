import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export function installation(raw, checkout) {
  let settings;
  try {
    settings = JSON.parse(raw);
  } catch {
    throw new Error("Missing or invalid WORKER_INSTALLATION_JSON build secret");
  }
  if (!settings || typeof settings !== "object" ||
      typeof settings.worker !== "string" || !/^[a-z0-9][a-z0-9-]{0,62}$/.test(settings.worker) ||
      typeof settings.namespace !== "string" || !/^[0-9a-f]{32}$/.test(settings.namespace) ||
      typeof settings.repository !== "string" || !/^[\w.-]+\/[\w.-]+$/.test(settings.repository)) {
    throw new Error("Invalid worker, namespace or repository in build secret");
  }
  if (checkout.toLowerCase() !== settings.repository.toLowerCase()) {
    throw new Error("Build secret belongs to another repository");
  }
  return settings;
}

function checkoutRepository() {
  if (process.env.GITHUB_ACTIONS === "true") return process.env.GITHUB_REPOSITORY || "";
  const origin = spawnSync("git", ["remote", "get-url", "origin"], { encoding: "utf8" });
  const url = (origin.stdout || "").trim().replace(/^git@github\.com:/, "https://github.com/");
  try {
    const remote = new URL(url);
    if (remote.hostname === "github.com")
      return remote.pathname.replace(/^\//, "").replace(/\.git$/, "");
  } catch { /* Avoid printing authenticated remote URLs. */ }
  throw new Error("Cannot verify the connected GitHub checkout");
}

function deploy() {
  const dryRun = process.argv[2] === "--dry-run";
  const cfg = installation(process.env.WORKER_INSTALLATION_JSON, checkoutRepository());
  const directory = mkdtempSync(join(tmpdir(), "worker-installation-"));
  try {
    const configPath = join(directory, "wrangler.json");
    const secretsPath = join(directory, "runtime.json");
    const config = JSON.parse(readFileSync(new URL("./worker-template.json", import.meta.url)));
    config.name = cfg.worker;
    config.main = fileURLToPath(new URL("./src/index.js", import.meta.url));
    config.kv_namespaces = [{ binding: "WATCHDOG_KV", id: cfg.namespace }];
    writeFileSync(configPath, JSON.stringify(config), { mode: 0o600 });
    writeFileSync(secretsPath, JSON.stringify({ TARGET_REPOSITORY: cfg.repository }), { mode: 0o600 });
    const args = ["--yes", "wrangler", "deploy", "--config", configPath];
    if (dryRun) args.push("--dry-run", "--outdir", join(directory, "bundle"));
    else args.push("--secrets-file", secretsPath);
    const result = spawnSync("npx", args, { stdio: dryRun ? "inherit" : "ignore" });
    if (result.status !== 0) throw new Error("Wrangler deployment failed. Check build permissions and resource settings.");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { deploy(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
