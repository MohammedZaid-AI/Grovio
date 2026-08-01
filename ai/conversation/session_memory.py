from datetime import datetime


class SessionMemory:
    """
    Short-lived working memory for an in-flight conversation, keyed by phone.

    This is NOT the concierge's long-term memory — it is wiped on restart and
    holds only what the current exchange needs. Durable per-user memory
    (preferences, allergies, budget, order history) arrives in Phase 4.
    """

    def __init__(self):
        self.sessions = {}

    def get(self, phone):
        if phone not in self.sessions:
            self.sessions[phone] = {
                "last_message": None,
                "last_response": None,
                "updated_at": datetime.now(),
            }
        return self.sessions[phone]

    def update(self, phone, **kwargs):
        session = self.get(phone)
        session.update(kwargs)
        session["updated_at"] = datetime.now()

    def clear(self, phone):
        self.sessions.pop(phone, None)


memory = SessionMemory()
