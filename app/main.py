"""FastAPI app: serves the chat page and the /chat endpoint."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import crm, generation, knowledge, planner

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model and index knowledge/ once, before serving requests.
    knowledge.get_store()
    yield


app = FastAPI(title="CRM Assistant (demo skeleton)", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat")
def chat(request: ChatRequest):
    plan = planner.plan(request.message)
    route = plan["route"]
    passages = []
    generation_info = None

    if route == "action":
        function_name, result = crm.pick_placeholder(request.message)
        reply = (
            f"Placeholder function that would run: {function_name}()\n"
            f"Demo result:\n{json.dumps(result, indent=2)}"
        )
    elif route == "knowledge":
        passages = knowledge.search(request.message)
        generated = generation.generate_answer(request.message, passages)
        generation_info = generated.as_dict()
        reply = knowledge.format_reply(request.message, passages, generated)
    else:
        reply = (
            "What would you like to do? You can ask me to find, add or update "
            "a contact, or ask how to do something."
        )

    return {
        "reply": reply,
        "route": route,
        "route_source": plan["route_source"],
        "passages": passages,
        "generation": generation_info,
    }
