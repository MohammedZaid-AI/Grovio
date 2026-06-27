from ai.conversation.intent_router import IntentRouter
from ai.conversation.session import session
from ai.conversation.chunker import chunker

from backend.chat import process_message


class ConversationEngine:

    """
    Main conversation engine.

    Responsible for:

    • Intent routing
    • Conversation history
    • Long response chunking
    • Continue support
    • Context management
    """

    def __init__(self):

        self.router = IntentRouter()

    # ---------------------------------------
    # Process User Message
    # ---------------------------------------

    def process(

        self,

        phone,

        message

    ):

        message = message.strip()

        # -----------------------------------
        # Continue
        # -----------------------------------

        if message.lower() == "continue":

            next_chunk = session.next_chunk(phone)

            if next_chunk:

                return next_chunk

            return (
                "✅ End of report."
            )

        # -----------------------------------
        # Detect Intent
        # -----------------------------------

        intent = self.router.detect(message)

        session.set_intent(

            phone,

            intent

        )

        # -----------------------------------
        # Generate Response
        # -----------------------------------

        response = process_message(message)

        # -----------------------------------
        # Save History
        # -----------------------------------

        session.add_message(

            phone,

            message,

            response

        )

        # -----------------------------------
        # Chunk Response
        # -----------------------------------

        chunks = chunker.split(response)

        session.save_chunks(

            phone,

            chunks

        )

        return session.next_chunk(phone)


engine = ConversationEngine()