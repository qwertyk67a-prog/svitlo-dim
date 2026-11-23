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

async function githubHealthy(repository, token, fetchFn, nowMs) {
  const url = "https://api.github.com/repos/" + repository +
    "/actions/workflows/check.yml/runs?per_page=20&branch=main";
  const standardHeaders = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "svitlo-external-watchdog",
  };
  let response;
  try {
    response = await fetchFn(url, {
      headers: token ? { ...standardHeaders, Authorization: "Bearer " + token } : standardHeaders,
      signal: AbortSignal.timeout(15000),
    });
    if (token && (response.status === 401 || response.status === 403)) {
      // Public repository history remains readable if the dispatch PAT
      // expires or is revoked. Do not use it for Telegram requests.
      response = await fetchFn(url, { headers: standardHeaders, signal: AbortSignal.timeout(15000) });
    }
    if (!response.ok) return false;
    const value = await response.json();
    return assessRuns(value?.workflow_runs, nowMs);
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

  await sendTelegram(
    env,
    "⚠️ Контроль запусків: не вдається підтвердити роботу автоматичних перевірок. " +
    "Немає недавнього успішного запуску або GitHub API недоступний. " +
    "Це НЕ підтверджує відключення електрики. Перевірте GitHub Actions.",
    fetchFn
  );
  await env.WATCHDOG_KV.put(
    key, JSON.stringify(record(
      "alerted",
      old?.sinceMs || nowMs,
      nowMs
    ))
  );
  return "alert_sent";
}
