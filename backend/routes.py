from fastapi import APIRouter, Form
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from ai.langgraph.graph import graph
from ai.invoice.pipeline import InvoicePipeline

router = APIRouter()

pipeline = InvoicePipeline()


# ----------------------------------------
# Twilio Response Helper
# ----------------------------------------

def whatsapp_reply(message: str):

    twiml = MessagingResponse()

    twiml.message(message)

    return Response(

        content=str(twiml),

        media_type="application/xml"

    )


# ----------------------------------------
# WhatsApp Webhook
# ----------------------------------------

@router.post("/webhook")
async def webhook(

    Body: str = Form(""),

    NumMedia: int = Form(0),

    MediaUrl0: str = Form(None),

    MediaContentType0: str = Form(None)

):

    print("=" * 70)
    print("Incoming WhatsApp Message")
    print("Body:", Body)
    print("Media:", NumMedia)
    print("=" * 70)

    # ----------------------------------------
    # Invoice Processing
    # ----------------------------------------

    if NumMedia > 0:

        try:

            result = pipeline.process(

                MediaUrl0,

                MediaContentType0

            )

            if result.get("success"):

                reply = (
                    "✅ Invoice processed successfully.\n\n"
                    "Inventory updated.\n"
                    "Price history updated."
                )

            else:

                reply = (
                    "❌ Invoice processing failed.\n\n"
                    f"{result.get('message', 'Unknown error.')}"
                )

        except Exception as e:

            print("Invoice Error:", e)

            reply = (
                "❌ Unable to process invoice.\n\n"
                f"{str(e)}"
            )

        return whatsapp_reply(reply)

    # ----------------------------------------
    # AI Conversation
    # ----------------------------------------

    try:

        result = graph.invoke(

            {

                "message": Body,

                "selected_agents": [],

                "results": {},

                "response": ""

            }

        )

        print("\nLangGraph Result")
        print(result)
        print()

        reply = result.get(

            "response",

            "Sorry, I couldn't generate a response."

        )

        # WhatsApp messages shouldn't be huge
        MAX_LENGTH = 1400

        if len(reply) > MAX_LENGTH:

            reply = (

                reply[:MAX_LENGTH]

                + "\n\n...Reply 'continue' to receive more."

            )

        return whatsapp_reply(reply)

    except Exception as e:

        print("Graph Error:", e)

        return whatsapp_reply(

            "❌ Grovio encountered an unexpected error while processing your request."

        )