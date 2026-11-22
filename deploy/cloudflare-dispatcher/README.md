# Configure an independent five-minute scheduler

Use one Worker, one KV namespace and one repository per installation. Copy the default branch into a new GitHub repository without old history or the notification-state branch. Run the repository's empty-history initializer once. Configure and verify the provider and destination before activating production.

Connect the Worker to that repository's main branch in Cloudflare Workers Builds. Set the root directory to deploy/cloudflare-dispatcher and the production deploy command to `node deploy.mjs`. Disable deployments from other branches for this production project.

Under Build variables and secrets add the encrypted build secret WORKER_INSTALLATION_JSON. Its fields are worker (the connected Worker name), namespace (its KV namespace ID), and repository (owner/name). Use a unique Worker and KV namespace for every separate property. The script verifies the checkout before deployment. Temporary configuration supplies the Worker and KV binding. The repository is installed as the runtime Secret TARGET_REPOSITORY.

Under Runtime variables and secrets, create GH_WORKFLOW_TOKEN (a GitHub token scoped to this repository with Actions write permission), WATCHDOG_TELEGRAM_BOT_TOKEN and WATCHDOG_TELEGRAM_CHAT_ID (the private administrator chat). During migration retain the current Worker, namespace, token and chat to preserve existing health history. The GitHub Actions secrets used for resident notifications are independent.

A healthy new installation sends a private setup confirmation. A 20-minute absence of successful GitHub checks and two consecutive failed health assessments trigger an administrator alert. When checks resume, the Worker sends a recovery notice. An unavailable Cloudflare Worker cannot report its own failure.
