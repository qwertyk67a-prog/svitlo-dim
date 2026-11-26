/**
 * External health verification for SVITLO-DIM.
 * The code is inert unless WATCHDOG_ENABLED === "true".
 * GitHub, Telegram and Cloudflare KV are separate failure domains.
 * No address, queue or production Telegram token is stored here.
 */
const HEALTH_WINDOW_MS = 20 * 60 * 1000;
const REMINDER_MS = 60 * 60 * 1000;
const STATE_PREFIX = "svitlo:health:v1:";

function verifyRepository(value) {
  const repository = String(value || "");
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new Error("Watchdog repository invalid");
  }
  return repository;
}

export function assessRuns(runs, nowMs) {
  if (!Array.isArray(runs)) return false;
  return runs.some((run) => {
    if (!run || run.status !== "completed" ||
        run.conclusion !== "success" || run.head_branch !== "main" ||
        !["workflow_dispatch", "schedule"].includes(run.event)) return false;
    const created = Date.parse(run.created_at || "");
    const age = nowMs - created;
    return Number.isFinite(age) && age >= -60000 &&
      age <= HEALTH_WINDOW_MS;
  });
}

async function readGitHub(endpoint, token, fetchFn) {
  const standardHeaders = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "svitlo-external-watchdog",
  };

  let response = await fetchFn(endpoint, {
      headers: token ? { ...standardHeaders, Authorization: "Bearer " + token } : standardHeaders,
      signal: AbortSignal.timeout(15000),
    });
    if (token && (response.status === 401 || response.status === 403)) {
      response = await fetchFn(endpoint, { headers: standardHeaders, signal: AbortSignal.timeout(15000) });
    }
    return response.ok ? response.json() : null;

}

async function githubHealthy(repository, token, fetchFn, nowMs) {
  const url = "https://api.github.com/repos/" + repository +
    "/actions/workflows/check.yml/runs?per_page=20&branch=main";
  const read = endpoint => readGitHub(endpoint, token, fetchFn);
  try {
    const history = await read(url);
    if (!Array.isArray(history?.workflow_runs)) return false;
    const recent = history.workflow_runs.filter(run => assessRuns([run], nowMs));
    // A dispatch fails when disabled. A native schedule can succeed after skipping the monitor.
    if (recent.some(run => run.event === "workflow_dispatch")) return true;
    for (const run of recent) {
      if (!Number.isSafeInteger(run.id) || run.id <= 0) continue;
      const details = await read("https://api.github.com/repos/" + repository +
        "/actions/runs/" + run.id + "/jobs");
      if (details?.jobs?.some(job => job.conclusion === "success" &&
          job.steps?.some(step => step.name === "Read configured source and notify Telegram" &&
            step.conclusion === "success"))) return true;
    }
    return false;
  } catch (_e) {
    // GitHub might be offline. Let independent Telegram report this.
    return false;
  }
}

async function sendTelegram(env, text, fetchFn) {
  const token = String(env.WATCHDOG_TELEGRAM_BOT_TOKEN || "");
  const chat = String(env.WATCHDOG_TELEGRAM_CHAT_ID || "");
  if (!token || !chat) {
    throw new Error("Watchdog Telegram Secrets missing");
  }
  if (!/^[1-9]\d*$/.test(chat)) throw new Error("Private administrator chat ID required");
  // Never log the secret, URL, response body or Telegram chat ID.
  const url = "https://api.telegram.org/bot" + token + "/sendMessage";
  const response = await fetchFn(url, {
    method: "POST",
    signal: AbortSignal.timeout(15000),
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chat,
      text,
      disable_web_page_preview: true,
    }),
  });
  if (!response.ok) throw new Error("Watchdog Telegram delivery failed");
  // Telegram may reply with HTTP 200 and {"ok":false}.
  const data = await response.json();
  if (data?.ok !== true) {
    throw new Error("Watchdog Telegram refused delivery");
  }
}

function record(status, sinceMs, lastAlertMs = 0) {
  return { status, sinceMs, lastAlertMs };
}

export async function checkExternalHealth(env, fetchFn = fetch, nowMs = Date.now()) {
  if (env.WATCHDOG_ENABLED !== "true") return "disabled";
  const repository = verifyRepository(env.TARGET_REPOSITORY);
  if (!env.WATCHDOG_KV ||
      !env.WATCHDOG_TELEGRAM_BOT_TOKEN || !env.WATCHDOG_TELEGRAM_CHAT_ID) {
    throw new Error("Watchdog is enabled without required KV or Secrets");
  }
  const key = STATE_PREFIX + repository;
  const old = (await env.WATCHDOG_KV.get(key, "json")) || null;
  const healthy = await githubHealthy(
    repository, env.GH_WORKFLOW_TOKEN, fetchFn, nowMs
  );

  if (healthy) {
    if (old?.status === "alerted") {
      await sendTelegram(
        env,
        "✅ Контроль запусків: успішні автоматичні перевірки відновлено. " +
        "Це не підтверджує наявність електрики за конкретною адресою.",
        fetchFn
      );
      await env.WATCHDOG_KV.put(
        key, JSON.stringify(record("healthy", nowMs))
      );
      return "recovered";
    }
    if (!old && env.WATCHDOG_CONFIRM_SETUP === "true") {
      await sendTelegram(env, "✅ Контроль запусків: тестове особисте повідомлення. Контроль запусків GitHub увімкнено.", fetchFn);
    }
    if (old?.status === "suspect" || !old) {
      await env.WATCHDOG_KV.put(
        key, JSON.stringify(record("healthy", nowMs))
      );
    }
    return "healthy";
  }

  if (!old || old.status === "healthy") {
    // Confirm across two consecutive 5-minute Cron invocations.
    await env.WATCHDOG_KV.put(
      key, JSON.stringify(record("suspect", nowMs))
    );
    return "suspect";
  }
  if (old.status === "alerted" &&
      nowMs - old.lastAlertMs < REMINDER_MS) {
    return "alerted";
  }

  let detail = "Не вдається підтвердити недавній успішний запуск.";
  try {
    const base = "https://api.github.com/repos/" + repository + "/actions";
    const history = await readGitHub(base + "/workflows/check.yml/runs?per_page=5&branch=main",
      env.GH_WORKFLOW_TOKEN, fetchFn);
    const failed = history?.workflow_runs?.find(run => run.status === "completed");
    const age = nowMs - Date.parse(failed?.created_at);
    if (failed?.conclusion === "failure" && failed.head_branch === "main" &&
        Number.isSafeInteger(failed.id) && failed.id > 0 && age >= 0 && age <= HEALTH_WINDOW_MS) {
      detail = "Запуски тривають. Остання завершена перевірка має помилку.";
      const report = await readGitHub(base + "/runs/" + failed.id + "/jobs", env.GH_WORKFLOW_TOKEN, fetchFn);
      if (report?.jobs?.some(job => job.steps?.some(step =>
          step.name === "Address lookup failed" && step.conclusion === "success"))) {
        detail += " Оператор не підтвердив чергу будинку.";
      }
    }
  } catch {
    // The administrator still needs the original alert if GitHub cannot provide details.
  }
  await sendTelegram(env, "⚠️ Контроль запусків: " + detail +
    " Перевірте GitHub Actions. Стан електропостачання будинку невідомий.", fetchFn);
  await env.WATCHDOG_KV.put(
    key, JSON.stringify(record(
      "alerted",
      old?.sinceMs || nowMs,
      nowMs
    ))
  );
  return "alert_sent";
}

export async function checkPinHealth(env, fetchFn = fetch, nowMs = Date.now()) {
  const repository = verifyRepository(env.TARGET_REPOSITORY);
  const root = "https://api.github.com/repos/" + repository + "/actions";
  const read = path => readGitHub(root + path, env.GH_WORKFLOW_TOKEN, fetchFn);
  const history = await read("/workflows/check.yml/runs?per_page=20&branch=main");
  const runs = (history?.workflow_runs || []).filter(run =>
    Number.isSafeInteger(run.id) && run.id > 0 && run.status === "completed" &&
    run.head_branch === "main" && ["workflow_dispatch", "schedule"].includes(run.event) &&
    nowMs >= Date.parse(run.created_at) && nowMs - Date.parse(run.created_at) <= HEALTH_WINDOW_MS
  ).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id);
  const distinct = [...new Set(runs.map(run => run.id))];
  async function observation(id) {
    const data = await read("/runs/" + id + "/jobs");
    const steps = data?.jobs?.flatMap(job => job.steps || []) || [];
    const passed = name => steps.some(step => step.name === name && step.conclusion === "success");
    if (passed("Schedule pin confirmed")) return "ok";
    if (passed("Schedule pin failed")) return "failed";
    return "unknown";
  }
  if (!distinct.length) return "unknown";
  const latest = await observation(distinct[0]);
  if (latest === "unknown") return latest;
  const key = "svitlo:pin:v1:" + repository;
  const alerted = await env.WATCHDOG_KV.get(key) === "alerted";
  if (latest === "ok") {
    if (alerted) {
      await sendTelegram(env, "✅ Закріплення графіка підтверджено. Роботу відновлено.", fetchFn);
      await env.WATCHDOG_KV.delete(key);
    }
    return "ok";
  }
  if (alerted || distinct.length < 2 || await observation(distinct[1]) !== "failed") return "pending";
  await sendTelegram(env, "⚠️ Дві перевірки поспіль не підтвердили закріплення графіка. Перевірте закріплене повідомлення та права бота.", fetchFn);
  await env.WATCHDOG_KV.put(key, "alerted");
  return "alerted";
}
