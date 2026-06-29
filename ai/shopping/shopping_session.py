class ShoppingSession:

    """
    Keeps track of a user's
    current shopping flow.
    """

    def __init__(self):

        self.sessions = {}

    def start(
        self,
        phone,
        items
    ):

        self.sessions[phone] = {

            "items": items,

            "current": 0,

            "selected": []

        }

    def get(self, phone):

        return self.sessions.get(phone)

    def save_selection(

        self,

        phone,

        product

    ):

        session = self.sessions[phone]

        session["selected"].append(product)

        session["current"] += 1

    def next_item(self, phone):

        session = self.sessions[phone]

        if session["current"] >= len(session["items"]):

            return None

        return session["items"][

            session["current"]

        ]

    def finish(self, phone):

        return self.sessions.pop(

            phone,

            None

        )


shopping_session = ShoppingSession()