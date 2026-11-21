/**
 * Optional five-minute trigger for an existing GitHub workflow.
 * Contains no address, provider URL, Telegram token or chat ID.
 * This Worker exposes no HTTP request handler.
 */
export default {
  async scheduled(_event, env, _ctx) {
    if (env.ENABLED !== "true") {
      console.log("Dispatch disabled");
      return;
    }
    const repository = String(env.GH_REPOSITORY || "");
    const token = env.GH_WORKFLOW_TOKEN;
    const ref = String(env.GH_REF || "main");
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
      throw new Error("Invalid GH_REPOSITORY");
    }
    if (!/^[A-Za-z0-9_.\/-]{1,200}$/.test(ref) || ref.includes("..")) {
      throw new Error("Invalid GH_REF");
    }
    if (!token) {
      throw new Error("Missing GH_WORKFLOW_TOKEN");
    }
    const url = "https://api.github.com/repos/" + repository +
      "/actions/workflows/check.yml/dispatches";
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "svitlo-cron-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref }),
    });
    if (response.status !== 204) {
      // Do not log response bodies or secrets.
      throw new Error("GitHub dispatch failed: HTTP " + response.status);
    }
    console.log("Workflow dispatch accepted");
  },
};
