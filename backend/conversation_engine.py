from ai.conversation.session import session
from ai.conversation.chunker import chunker
from ai.conversation.session_memory import memory

from backend.chat import process_message


class ConversationEngine:
    """
    Main conversation engine.

    Responsible for:

    • Session Memory
    • Conversation History
    • Response Chunking
    • Continue Support
    • Calling LangGraph
    """

    def __init__(self):

        pass

    # --------------------------------------------------
    # Process User Message
    # --------------------------------------------------

    def process(

        self,

        phone,

        message

    ):

        message = message.strip()

        # ---------------------------------------
        # Ensure Session Exists
        # ---------------------------------------

        memory.get(phone)

        # ---------------------------------------
        # Continue Support
        # ---------------------------------------

        if message.lower() == "continue":

            next_chunk = session.next_chunk(phone)

            if next_chunk:

                return next_chunk

            return "✅ End of report."

        # ---------------------------------------
        # Generate AI Response
        # ---------------------------------------

        response = process_message(

            phone=phone,

            message=message

        )

        # ---------------------------------------
        # Store Conversation History
        # ---------------------------------------

        session.add_message(

            phone,

            message,

            response

        )

        # ---------------------------------------
        # Store Session Memory
        # ---------------------------------------

        memory.update(

            phone,

            last_message=message,

            last_response=response

        )

        # ---------------------------------------
        # Chunk Long Responses
        # ---------------------------------------

        chunks = chunker.split(response)

        session.save_chunks(

            phone,

            chunks

        )

        return session.next_chunk(phone)


engine = ConversationEngine()