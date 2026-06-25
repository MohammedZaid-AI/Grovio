from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse

from backend.chat import process_message

router = APIRouter()


@router.post("/webhook")
async def webhook(

    Body: str = Form(...)
):

    reply = process_message(Body)

    return PlainTextResponse(reply)