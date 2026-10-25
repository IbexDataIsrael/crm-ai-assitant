Spec: server profile for the AI assistant (private deployment)
Don't push. Don't commit. Stop for review.
Ground rules
Preserve all existing work: the contact-draft bridge, the SCRAM database setup, and the login / menu / conversation work.
No server changes, DNS, Caddy or deployment. No live CRM or AI-provider calls; mock external HTTP in tests.
Never delete an existing Docker volume. No docker compose down -v, no global prune. Any test volume is new, separately named, and left in place for review.
Goal
Prepare this project to run privately on the server as a standalone Docker service. Later clients (the CRM backend, maybe WhatsApp) will call it. This task ends with files, tests and measurements ready for review, not with a deployment.
What exists today
docker-compose.yml is for local development: source bind mount, --reload, app on 127.0.0.1:8011.
PostgreSQL + pgvector (pg18, pinned image) with SCRAM password login: mounted scripts/db/pg_hba.conf, restricted roles crm_app and crm_test, admin only through the container's local socket. A fresh empty volume fails closed until passwords exist.
Passwords are written by scripts/set-db-passwords.ps1, Windows PowerShell only. No Linux version.
Endpoints: GET / serves the chat page (static/index.html, same FastAPI app). POST /chat answers. POST /auth/login proxies CRM login with redirects off and stores nothing. POST /assistant/turn runs the menu and conversation. POST /crm/contact-drafts/proposals and POST /crm/contact-drafts/proposals/{id}/confirm forward the user's CRM token to CRM_API_URL.
CRM_API_URL is a required environment variable with no default. GEMINI_API_KEY is optional: without it, field extraction still works one field at a time; the Ask flow behaves differently without a provider.
The conversation and token handling are stateless: the only data volume is Postgres/pgvector. No session storage to migrate.
No health endpoint. No server compose profile.
Server side is ready: the repo is checked out at /home/zvi/crm-ai-assitant with a read-only deploy key. Nothing runs there yet.
Build
Server compose profile (docker-compose.server.yml). Same image. No source bind mount, no --reload. restart: unless-stopped. Its own compose project name, its own named database volume, its own network. Publish the app on 127.0.0.1 only, no public port. Local development stays unchanged.
Private access only. The network is the boundary at this stage: the app is reachable only through an SSH tunnel, the same pattern as the load-test UI on this server. Document the tunnel command in DEPLOY.md. No service token and no new login flow in this task: a browser cannot hold a service secret, and the login form already handles users. One possible later design is a server-to-server token when a real backend client (the CRM backend or a WhatsApp gateway) connects; that token would live in the calling backend's environment, never in browser code. It is a deferred possibility, not a committed requirement.
Linux secrets bootstrap (scripts/set-db-passwords.sh). Generates random SCRAM passwords, writes them into the server .env without printing them, and refuses to run if passwords already exist unless called with an explicit rotate flag. Never logs or displays secret values. Prove first boot on a new, separately named empty volume and leave that volume for review.
GET /health. Returns ok or not-ok only. No data, no versions, no secrets, no calls to an AI provider. Do not add any other unauthenticated endpoint for smoke purposes.
Offline model proof. Run the model-loaded path with the model-download service unreachable and report exactly what passed. If it cannot be proven, say so instead of claiming it.
Resource measurements. Build the image, run the server profile, load the model and index the guides. Report image size from docker images and container memory from docker stats. Measurements only, no estimates.
CRM_API_URL stays a required environment variable with no default and no hardcoded address. The server value is chosen at deploy time.
DEPLOY.md: the server steps in order: create .env with the Linux script, compose up with the server profile, open the tunnel, check /health. The provider key is optional: keep the provider configuration already chosen, and make no provider calls during this preparation. Keep it short.
Design choices to label
The loopback port number.
Project and volume names.
Whether /health also checks the database.
Known limits
Memory fit on the server is unproven until the measurements exist. The box has about 2.8 GiB available and already uses swap.
Private access is protected by the loopback + SSH-tunnel boundary, not by login: the login proxy and guide chat answer anyone who reaches the page. CRM actions still require the user's own CRM token. Public exposure would need auth and quota hardening that is not established - a separate future decision.
The vector database volume has no backup routine yet.
Not in this task
Deployment, DNS, Caddy, service tokens, WhatsApp, changes to the CRM, provider changes.
Done when
The server profile builds and runs locally with the model loaded, and /health answers.
Tests pass with no new failures. The 3 known retrieval failures stay as they are and are reported as-is.
You report: files changed, actual test results, measured image size and memory, the bootstrap commands, and anything you could not prove.
Nothing committed, nothing pushed.
