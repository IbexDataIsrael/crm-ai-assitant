Don't push - stop for my review.
Create Git Repo like the other projects under IbexDataIsrael 

Work only inside this new crm-assistant folder. Do not read or change the minicrm project or anything outside this folder.

Build the first local skeleton of a conversational CRM assistant using Python, FastAPI and one plain HTML page. Windows machine: use PowerShell commands. Check Python availability first. Create a local .venv and install only fastapi and uvicorn. Add requirements.txt and .gitignore excluding .venv, .env and _pycache_.

The goal is a running chat page with clearly labeled placeholders. No real AI, RAG or CRM connection in this step.

Files:
1. app/main.py: serve the page at / and POST /chat. Accept a message and return a reply, route and route_source.
2. app/planner.py: temporary keyword routing. Contact/find/update/add goes to action; how/instructions goes to knowledge; otherwise clarify. Label route_source as keyword-stub. This will become a model call later.
3. app/crm.py: placeholder functions find_contact, update_contact_field and create_contact_draft. Return clearly labeled demo data only. Never claim a real record was changed.
4. app/knowledge.py and knowledge/sample.md: short fictional volunteer instructions, clearly labeled sample data. For now return the sample text. Clearly state this is NOT retrieval or RAG yet; embeddings and vector search come later.
5. static/index.html: readable chat page with large text, input and send button. Show the selected route and "Demo skeleton - no AI or live CRM connection" beside replies.

Action replies show which placeholder function would run and its demo result. Knowledge replies show the sample instructions. Clarify replies ask what the user wants to do. Keep the implementation small and easy to explain.

No API keys, external service calls, database, Docker, cloud setup, GitHub repo or production changes. Installing the two Python packages is the only network use needed.

Run local checks for all three routes. Open the page and inspect it if browser access is available; otherwise say visual inspection is still pending.

At the end, show the file map, exact PowerShell command to start the app, local URL and one example message for each route. Explain briefly what each file does. Stop for my review.