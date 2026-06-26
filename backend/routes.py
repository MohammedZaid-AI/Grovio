from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse
from ai.langgraph.graph import graph
from backend.chat import process_message

router = APIRouter()


@router.post("/webhook")
async def webhook(

    Body: str = Form(...)
):


    result = graph.invoke(

        {

            "message": message,

            "selected_agents": [],

            "results": {},

            "response": ""

        }

    )

    reply = result["response"]

    return PlainTextResponse(reply)