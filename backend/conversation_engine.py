from ai.conversation.session import session
from ai.conversation.chunker import chunker
from ai.conversation.session_memory import memory

from ai.concierge import respond


class ConversationEngine:
    """
    Main Conversation Engine.

    Responsibilities

    • Conversation history
    • Session memory
    • Response chunking
    • Continue support

    Business logic is handled by ai.concierge
    """

    def __init__(self):

        pass

    # --------------------------------------------------
    # Process Message
    # --------------------------------------------------

    async def process(

        self,

        phone,

        message

    ):

        message = message.strip()

        # ---------------------------------------
        # Ensure Memory Exists
        # ---------------------------------------

        memory.get(phone)

        # ---------------------------------------
        # Continue Long Response
        # ---------------------------------------

        if message.lower() == "continue":

            next_chunk = session.next_chunk(phone)

            if next_chunk:

                return next_chunk

            return "✅ End of response."

        # ---------------------------------------
        # Process Business Logic
        # ---------------------------------------

        response = await respond(

            phone=phone,

            message=message

        )

        # ---------------------------------------
        # Save Conversation
        # ---------------------------------------

        session.add_message(

            phone,

            message,

            response

        )

        # ---------------------------------------
        # Update Memory
        # ---------------------------------------

        memory.update(

            phone,

            last_message=message,

            last_response=response

        )

        # ---------------------------------------
        # Chunk Long Response
        # ---------------------------------------

        chunks = chunker.split(

            response

        )

        session.save_chunks(

            phone,

            chunks

        )

        return session.next_chunk(phone)


engine = ConversationEngine()