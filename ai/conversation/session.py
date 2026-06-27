from datetime import datetime


class SessionManager:
    """
    Stores active WhatsApp conversations.

    Later this can be replaced with Redis
    without changing the rest of Grovio.
    """

    def __init__(self):

        self.sessions = {}

    # -------------------------
    # Get Session
    # -------------------------

    def get(self, phone):

        if phone not in self.sessions:

            self.sessions[phone] = {

                "created_at": datetime.now(),

                "updated_at": datetime.now(),

                "last_intent": None,

                "history": [],

                "report_chunks": [],

                "chunk_index": 0,

                "context": {}

            }

        return self.sessions[phone]

    # -------------------------
    # Update Intent
    # -------------------------

    def set_intent(

        self,

        phone,

        intent

    ):

        session = self.get(phone)

        session["last_intent"] = intent

        session["updated_at"] = datetime.now()

    # -------------------------
    # Save Conversation
    # -------------------------

    def add_message(

        self,

        phone,

        user,

        assistant

    ):

        session = self.get(phone)

        session["history"].append({

            "user": user,

            "assistant": assistant,

            "time": datetime.now()

        })

        # Keep only latest 20

        session["history"] = session["history"][-20:]

    # -------------------------
    # Report Chunks
    # -------------------------

    def save_chunks(

        self,

        phone,

        chunks

    ):

        session = self.get(phone)

        session["report_chunks"] = chunks

        session["chunk_index"] = 0

    def next_chunk(

        self,

        phone

    ):

        session = self.get(phone)

        chunks = session["report_chunks"]

        index = session["chunk_index"]

        if index >= len(chunks):

            return None

        chunk = chunks[index]

        session["chunk_index"] += 1

        return chunk

    # -------------------------
    # Context
    # -------------------------

    def set_context(

        self,

        phone,

        key,

        value

    ):

        session = self.get(phone)

        session["context"][key] = value

    def get_context(

        self,

        phone,

        key,

        default=None

    ):

        session = self.get(phone)

        return session["context"].get(

            key,

            default

        )

    # -------------------------
    # Reset
    # -------------------------

    def clear(

        self,

        phone

    ):

        if phone in self.sessions:

            del self.sessions[phone]


session = SessionManager()


if __name__ == "__main__":

    phone = "9876543210"

    session.set_intent(

        phone,

        "inventory"

    )

    session.add_message(

        phone,

        "Inventory",

        "Milk : 10"

    )

    print(

        session.get(phone)

    )