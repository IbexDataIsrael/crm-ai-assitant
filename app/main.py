"""FastAPI app: serves the chat page and the /chat endpoint."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import crm, knowledge, planner

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="CRM Assistant (demo skeleton)")


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat")
def chat(request: ChatRequest):
    plan = planner.plan(request.message)
    route = plan["route"]

    if route == "action":
        function_name, result = crm.pick_placeholder(request.message)
        reply = (
            f"Placeholder function that would run: {function_name}()\n"
            f"Demo result:\n{json.dumps(result, indent=2)}"
        )
    elif route == "knowledge":
        reply = knowledge.get_instructions(request.message)
    else:
        reply = (
            "What would you like to do? You can ask me to find, add or update "
            "a contact, or ask how to do something."
        )

    return {"reply": reply, "route": route, "route_source": plan["route_source"]}
