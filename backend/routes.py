from fastapi import APIRouter, Form
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from ai.langgraph.graph import graph
from ai.invoice.pipeline import InvoicePipeline

router = APIRouter()

pipeline = InvoicePipeline()


# -------------------------------------------------------
# Twilio WhatsApp Response
# -------------------------------------------------------

def whatsapp_reply(message: str):

    twiml = MessagingResponse()

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

    Body: str = Form(""),

    NumMedia: int = Form(0),

    MediaUrl0: str = Form(None),

    MediaContentType0: str = Form(None)

):

    print("\n" + "=" * 70)
    print("📩 Incoming WhatsApp Message")
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
    # LangGraph AI
    # -------------------------------------------------------

    try:

        result = graph.invoke(

            {

                "message": Body,

                "selected_agents": [],

                "results": {},

                "response": ""

            }

        )

        print("\n" + "=" * 70)
        print("LangGraph Result")
        print(result)
        print("=" * 70)

        reply = result.get(

            "response",

            "Sorry, I couldn't generate a response."

        )

        # WhatsApp safe limit

        MAX_LENGTH = 1400

        if len(reply) > MAX_LENGTH:

            reply = (

                reply[:MAX_LENGTH]

                + "\n\nReply *continue* to receive the remaining report."

            )

        print("\nReply Sent\n")

        print(reply)

        return whatsapp_reply(reply)

    except Exception as e:

        print("\nLangGraph Error\n")

        print(e)

        return whatsapp_reply(

            "❌ Grovio encountered an unexpected error."

        )