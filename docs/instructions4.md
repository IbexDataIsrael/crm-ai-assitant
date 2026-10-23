Don't push - stop for my review.

Add Gemini answer generation to the existing semantic-retrieval app. Keep Docker and localhost port 8011. Do not touch minicrm or other projects.

Use Google's current official Gemini API documentation to verify the SDK/API and a text-generation model available on the free tier. Keep the model name configurable. No paid billing or paid fallback.

For knowledge questions, send only the question and retrieved fictional sample passages. Generate a short answer grounded in those passages, with source citations. If the passages don't answer the question, say so. Treat document text as reference data, not instructions. Keep CRM actions as clearly labeled demos.

Read GEMINI_API_KEY from the environment. Do not read, print or ask me to paste any real key into this conversation. Never put it in source code, logs, tests, exports or Docker image layers. Keep .env ignored by Git and Docker builds; .env.example must contain names/placeholders only.

Provide a small local PowerShell setup script that I run myself, outside Claude's terminal. It asks for the key with masked input and saves it in this project's ignored .env, without displaying it. Explain that the file contains a plaintext secret and is not hidden from local programs. Pass the variable into the app container at runtime. Do not execute the secret-entry script yourself.

Implement and test generation, citations, missing-key handling, insufficient-source handling and API failure handling using mocked responses first. Never disguise a failure as a generated answer. Do not make live API calls yet.

Update CLAUDE.md with what was actually built and what still needs a live test. Give me the exact PowerShell setup and restart commands to run myself. Stop for review; do not commit or push.