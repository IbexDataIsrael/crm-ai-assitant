Don't push - stop for my review.

In C:\projects\crm-assitant, make the existing app run in Docker. Do not rewrite its logic or remove its demo labels. Do not touch other projects. Leave .venv alone; Windows Python must not be required.

1. Check docker --version and docker compose version. If unavailable, stop and report. Do not install Docker or change Windows settings.

2. Add a Dockerfile using python:3.14-slim, working directory /code. Copy requirements.txt first, install its packages, then copy the application. Run uvicorn app.main:app with --host 0.0.0.0 --port 8000 --reload.

3. Add docker-compose.yml with one app service, built locally. Mount this project at /code for reload. Bind the port as "127.0.0.1:8000:8000" so it is local-only. No database or other services.

4. Add .dockerignore excluding .venv, .git, _pycache_ and .env. Never copy secrets into the image.

5. Build and start with Docker Compose. Pulling the base image and installing requirements inside it are allowed. If port 8000 is occupied, report it; do not kill an existing process or container.

6. Verify GET / and the action, knowledge and clarify chat routes through the running container. Check that the demo/no-AI labels remain. Report actual results, not assumed success. Open the page for visual inspection if possible; otherwise mark that check pending.

Do not commit, push, connect external AI/CRM services or change production.

When finished, show the changed files, route-test results, local URL and exact PowerShell start/stop commands. Explain briefly what the Dockerfile and Compose file each do. Stop for my review.