import test from "node:test";
import assert from "node:assert/strict";
import { assessRuns, checkExternalHealth } from "../src/watchdog.js";
import worker from "../src/index.js";

const T = Date.parse("2026-11-22T13:00:00Z");

function record(minutesAgo, nowMs = T) {
  return {
    status: "completed", conclusion: "success", event: "workflow_dispatch",
    head_branch: "main",
    created_at: new Date(nowMs - minutesAgo * 60000).toISOString(),
  };
}

function response(status, data = {}) {
  return { status, ok: status >= 200 && status < 300,
    async json() { return data; } };
}

function storage() {
  let state = null;
  let count = 0;
  return {
    async get(key, format) {
      assert.match(key, /^svitlo:health:v1:/);
      assert.equal(format, "json");
      return state;
    },
    async put(key, value) {
      assert.match(key, /^svitlo:health:v1:/);
      state = JSON.parse(value);
      count += 1;
    },
    get state() { return state; },
    get writes() { return count; },
  };
}

function env(kv) {
  return {
    TARGET_REPOSITORY: "test-org/test-repo",
    GH_WORKFLOW_TOKEN: "test-gh-token",
    WATCHDOG_ENABLED: "true",
    WATCHDOG_KV: kv,
    WATCHDOG_TELEGRAM_BOT_TOKEN: "test-telegram-token",
    WATCHDOG_TELEGRAM_CHAT_ID: "123456",
  };
}

test("recent successful main run is accepted; wrong branches and stale runs are ignored", () => {
  assert.equal(assessRuns([record(19)], T), true);
  assert.equal(assessRuns([record(21)], T), false);
  assert.equal(assessRuns([{...record(1), head_branch: "feature"}], T), false);
  assert.equal(assessRuns([{...record(1), conclusion: "failure"}], T), false);
  assert.equal(assessRuns([{...record(1), event: "push"}], T), false);
  assert.equal(assessRuns([], T), false);
});

test("disabled watchdog makes no network requests and needs no KV", async () => {
  assert.equal(await checkExternalHealth({ WATCHDOG_ENABLED: "false" },
    () => { throw new Error("must not call fetch"); }, T), "disabled");
});

test("two consecutive stale checks alert once, throttle hourly, then recover once", async () => {
  const kv = storage();
  const cfg = env(kv);
  let healthy = true;
  let alerts = [];
  const mockFetch = async (url, options) => {
    if (url.includes("api.github.com")) {
      return response(200, { workflow_runs: healthy ? [record(1)] : [] });
    }
    assert.match(url, /^https:\/\/api\.telegram\.org\/bot/);
    assert.equal(options.method, "POST");
    alerts.push(JSON.parse(options.body).text);
    return response(200, { ok: true });
  };

  assert.equal(await checkExternalHealth(cfg, mockFetch, T), "healthy");
  assert.equal(kv.writes, 1);
  healthy = false;
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 5 * 60000), "suspect");
  assert.equal(alerts.length, 0);
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 10 * 60000), "alert_sent");
  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /не вдається підтвердити/);
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 15 * 60000), "alerted");
  assert.equal(alerts.length, 1);
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 72 * 60000), "alert_sent");
  assert.equal(alerts.length, 2);
  healthy = true;
  const recoveryFetch = async (url, options) => {
    if (url.includes("api.github.com")) {
      return response(200, {workflow_runs: [record(1, T + 75 * 60000)]});
    }
    alerts.push(JSON.parse(options.body).text);
    return response(200, {ok: true});
  };
  assert.equal(await checkExternalHealth(cfg, recoveryFetch, T + 75 * 60000), "recovered");
  assert.equal(alerts.length, 3);
  assert.match(alerts[2], /відновлено/);
  assert.equal(await checkExternalHealth(cfg, recoveryFetch, T + 80 * 60000), "healthy");
  assert.equal(alerts.length, 3);
});

test("expired GitHub PAT still checks publicly accessible workflow history", async () => {
  const kv = storage(), cfg = env(kv);
  let requests = [];
  const mockFetch = async (url, options) => {
    requests.push(options.headers);
    return requests.length === 1 ? response(401) :
      response(200, {workflow_runs: [record(1)]});
  };
  assert.equal(await checkExternalHealth(cfg, mockFetch, T), "healthy");
  assert.equal(requests.length, 2);
  assert.ok("Authorization" in requests[0]);
  assert.ok(!("Authorization" in requests[1]));
});

test("GitHub unavailable alerts independently through Telegram on second check", async () => {
  const kv = storage(), cfg = env(kv);
  let sent = 0;
  const mockFetch = async (url) => {
    if (url.includes("api.github.com")) throw new Error("GitHub offline");
    sent += 1;
    return response(200, {ok: true});
  };
  assert.equal(await checkExternalHealth(cfg, mockFetch, T), "suspect");
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 300000), "alert_sent");
  assert.equal(sent, 1);
});

test("failed Telegram delivery does not advance throttle state", async () => {
  const kv = storage(), cfg = env(kv);
  let telegramFails = true, sent = 0;
  const mockFetch = async (url) => {
    if (url.includes("api.github.com")) return response(200, {workflow_runs: []});
    sent += 1;
    return telegramFails ? response(500) : response(200, {ok: true});
  };
  assert.equal(await checkExternalHealth(cfg, mockFetch, T), "suspect");
  await assert.rejects(
    checkExternalHealth(cfg, mockFetch, T + 300000),
    /Watchdog Telegram delivery failed/
  );
  assert.equal(kv.state.status, "suspect");
  telegramFails = false;
  assert.equal(await checkExternalHealth(cfg, mockFetch, T + 600000), "alert_sent");
  assert.equal(sent, 2);
});

test("disabled Worker preserves existing one-request GitHub dispatch", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push([url, opts]);
    return response(204);
  };
  try {
    await worker.scheduled({}, {
      ENABLED: "true", TARGET_REPOSITORY: "test-org/test-repo",
      GH_WORKFLOW_TOKEN: "test-gh-token", GH_REF: "main",
      WATCHDOG_ENABLED: "false",
    }, {});
    assert.equal(calls.length, 1);
    assert.match(calls[0][0], /\/actions\/workflows\/check.yml\/dispatches$/);
    assert.equal(calls[0][1].method, "POST");
  } finally {
    globalThis.fetch = original;
  }
});

test("Worker still inspects health if dispatch POST fails", async () => {
  const original = globalThis.fetch, kv = storage();
  globalThis.fetch = async (url) => {
    if (url.includes("/dispatches")) return response(401);
    if (url.includes("api.github.com")) return response(200, { workflow_runs: [] });
    return response(200, { ok: true });
  };
  const cfg = { ...env(kv), ENABLED: "true", GH_REF: "main" };
  try {
    await assert.rejects(worker.scheduled({}, cfg, {}), /GitHub dispatch failed/);
    assert.equal(kv.state.status, "suspect");
  } finally {
    globalThis.fetch = original;
  }
});

test("activation confirms private delivery once on a healthy installation", async () => {
  const kv = storage();
  const cfg = { ...env(kv), WATCHDOG_CONFIRM_SETUP: "true" };
  const messages = [];
  const mockFetch = async (url, options) => {
    if (url.includes("api.github.com")) return response(200, {workflow_runs: [record(1)]});
    messages.push(JSON.parse(options.body));
    return response(200, {ok: true});
  };
  await assert.rejects(checkExternalHealth({...cfg, WATCHDOG_TELEGRAM_CHAT_ID: "-123"}, mockFetch, T), /Private administrator/);
  assert.equal(kv.writes, 0);
  await checkExternalHealth(cfg, mockFetch, T);
  await checkExternalHealth(cfg, mockFetch, T + 300000);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].chat_id, cfg.WATCHDOG_TELEGRAM_CHAT_ID);
  assert.match(messages[0].text, /тестове особисте/);
});
