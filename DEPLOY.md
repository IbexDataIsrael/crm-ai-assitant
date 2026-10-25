# Server deployment (private, SSH tunnel only)

The assistant runs on the server as its own Docker service from
`docker-compose.server.yml`. It listens on **127.0.0.1:8020** only; you reach it
through an SSH tunnel (or, later, through the reverse proxy).

Login is enforced by the server (docs/instructions17.md): `/chat` and
`/assistant/turn` need the user's CRM token, checked with the CRM's
`GET /api/auth/me` on every request before any AI work. Open on purpose: the
page (`/`), `/health` and `/auth/login`. Login and chat are rate limited per
client. `/docs` and `/openapi.json` are off.

Repo on the server: `/home/zvi/crm-ai-assitant`. All commands run there.

## 1. Create .env (first time only)

```bash
bash scripts/set-db-passwords.sh
```

Writes three random database passwords into `.env` (mode 600, never displayed).
Refuses if `.env` already has them or the database volume already exists.

Then add the CRM address (required, no default) with an editor:

```bash
nano .env      # add: CRM_API_URL=<the CRM API base address, chosen at deploy time>
```

Optional: `GEMINI_API_KEY` (same provider setup as development: Gemini free
tier, `ANSWER_PROVIDER` unset). Without a key the app still runs: guide
questions show the retrieved passages only, and the add-contact conversation
asks one field at a time.

## 2. Start

```bash
docker compose -f docker-compose.server.yml up -d --build
docker compose -f docker-compose.server.yml logs -f app   # wait for "Application startup complete"
```

First boot creates the volume `crm-assistant-server_pgdata`, the roles and the
table (`scripts/db/init/10-roles.sh`), then the app loads the model and indexes
the guides (about 30 s). Containers restart by themselves (`unless-stopped`).

## 3. Open the tunnel (on your own computer)

```bash
ssh -N -L 8020:127.0.0.1:8020 zvi@<server>
```

Then open http://127.0.0.1:8020 in your browser. Ctrl+C closes the tunnel.

## 4. Check

```bash
curl -s http://127.0.0.1:8020/health      # {"status":"ok"}; anything else = not ok (503)
```

`/health` answers `ok` only if the app finished starting and the database holds
the guide rows. It calls no AI provider and no CRM.

## Behind a reverse proxy (later, before public access)

Rate limits count each client by address. Behind a proxy every request comes
from the proxy, so tell the app which address that is - only then is the
proxy's `X-Forwarded-For` believed:

```bash
# in .env: the proxy's address as the app container sees it (IP or CIDR)
TRUSTED_PROXIES=172.17.0.1
```

Once set, a request from that address without a valid `X-Forwarded-For` is
refused (400), so SSH-tunnel requests to the login and chat routes stop working
(they arrive from the same address). Leave it empty while access is
tunnel-only: then every tunnel user counts as one client. Find the address with
`docker network inspect crm-assistant-server_assistant` (Gateway) or from the
proxy's setup. A header sent by the user directly is never trusted.

## Local work: API docs

`/docs`, `/redoc` and `/openapi.json` are on only with `API_DOCS=on`.
docker-compose.yml sets it for development; the server profile never passes it.

## Later

- New code: `git pull && docker compose -f docker-compose.server.yml up -d --build`.
- New app/test database passwords: `bash scripts/set-db-passwords.sh --rotate`
  (containers running).
- Stop: `docker compose -f docker-compose.server.yml down` - keeps the volume.
  Never `down -v`: that deletes the database.
- Model offline check, with no network at all:
  `docker run --rm --network none crm-assistant-server-app python scripts/offline_model_check.py`
  (loads a second copy of the model: about 1 GB more RAM while it runs).
- No backups of the volume yet. The vectors can be rebuilt from the guides, but
  keep `.env`: the volume's passwords are only there. If it is lost, the admin
  can still reset them through the db container's local socket - ask before
  touching the volume.
