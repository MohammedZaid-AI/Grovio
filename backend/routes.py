from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import os

from backend.conversation_engine import engine
from ai.invoice.pipeline import InvoicePipeline

router = APIRouter()

pipeline = InvoicePipeline()


def split_message(text: str, max_length: int = 1500) -> list:
    if len(text) <= max_length:
        return [text]
    
    parts = []
    paragraphs = text.split("\n")
    current_part = []
    current_length = 0
    
    for paragraph in paragraphs:
        para_len = len(paragraph) + (1 if current_part else 0)
        
        if current_length + para_len > max_length:
            if current_part:
                parts.append("\n".join(current_part))
                current_part = []
                current_length = 0
            
            if len(paragraph) > max_length:
                words = paragraph.split(" ")
                current_word_part = []
                word_part_len = 0
                for word in words:
                    added_len = len(word) + (1 if current_word_part else 0)
                    if word_part_len + added_len > max_length:
                        if current_word_part:
                            parts.append(" ".join(current_word_part))
                        current_word_part = [word]
                        word_part_len = len(word)
                    else:
                        current_word_part.append(word)
                        word_part_len += added_len
                if current_word_part:
                    current_part.append(" ".join(current_word_part))
                    current_length += len(current_part[-1])
            else:
                current_part.append(paragraph)
                current_length = len(paragraph)
        else:
            current_part.append(paragraph)
            current_length += para_len
            
    if current_part:
        parts.append("\n".join(current_part))
        
    return parts

# -------------------------------------------------------
# Twilio WhatsApp Response
# -------------------------------------------------------

def whatsapp_reply(message: str or list):

    print(f"\n[DEBUG_FINAL_OUTPUT] {repr(message)}\n")

    twiml = MessagingResponse()

    if isinstance(message, list):
        for msg in message:
            twiml.message(msg)
    else:
        twiml.message(message)

    return Response(

        content=str(twiml),

        media_type="application/xml"

    )


# -------------------------------------------------------
# WhatsApp Webhook
# -------------------------------------------------------

@router.post("/webhook")
async def webhook(

    request: Request,

    Body: str = Form(""),

    NumMedia: int = Form(0),

    MediaUrl0: str = Form(None),

    MediaContentType0: str = Form(None),

    From: str = Form("")

):

    # -------------------------------------------------------
    # Twilio Signature Verification
    # -------------------------------------------------------
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if auth_token:
        validator = RequestValidator(auth_token)
        signature = request.headers.get("x-twilio-signature", "")
        proto = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
        url = f"{proto}://{host}{request.url.path}"
        
        form_data = await request.form()
        params = dict(form_data)
        
        if not validator.validate(url, params, signature):
            print("🚫 Webhook Signature Verification Failed!")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    print("\n" + "=" * 70)
    print("📩 Incoming WhatsApp Message")
    print("From :", From)
    print("Body :", Body)
    print("Media:", NumMedia)
    print("=" * 70)

    # -------------------------------------------------------
    # Invoice Processing
    # -------------------------------------------------------

    if NumMedia > 0:

        try:

            print("Processing Invoice...")

            result = pipeline.process(

                MediaUrl0,

                MediaContentType0

            )

            if result.get("success"):

                reply = (
                    "✅ Invoice processed successfully.\n\n"
                    "Inventory has been updated.\n"
                    "Price history has been updated."
                )

            else:

                reply = (
                    "❌ Invoice processing failed.\n\n"
                    f"{result.get('message', 'Unknown error.')}"
                )

        except Exception as e:

            print("\nInvoice Error\n")

            print(e)

            reply = (
                "❌ Unable to process invoice.\n\n"
                f"{str(e)}"
            )

        return whatsapp_reply(reply)

    # -------------------------------------------------------
    # Conversation Engine
    # -------------------------------------------------------

    try:

        reply = await engine.process(

            phone=From,

            message=Body

        )

        if not reply:

            reply = "Sorry, I couldn't generate a response."

        parts = split_message(reply, max_length=1500)

        print("\n" + "=" * 70)
        print("Reply Sent")
        for i, part in enumerate(parts):
            print(f"--- Part {i+1} ---")
            print(part)
        print("=" * 70)

        return whatsapp_reply(parts)

    except Exception as e:

        print("\nConversation Error\n")

        print(e)

        return whatsapp_reply(

            "❌ Grovio encountered an unexpected error."

        )