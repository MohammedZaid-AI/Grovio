class ShoppingSession:
    """
    Stores the shopping conversation state.
    """

    def __init__(self):

        self.sessions = {}

    # -----------------------------------
    # Start Session
    # -----------------------------------

    def start(self, phone, items):

        self.sessions[phone] = {

            "items": items,

            "current": 0,

            "selected": [],

            "options": [],

            "stage": "planning"

        }

    # -----------------------------------
    # Session
    # -----------------------------------

    def get(self, phone):

        return self.sessions.get(phone)

    def has_session(self, phone):

        return phone in self.sessions

    def end(self, phone):

        return self.sessions.pop(phone, None)

    # -----------------------------------
    # Stage
    # -----------------------------------

    def get_stage(self, phone):

        return self.sessions[phone]["stage"]

    def set_stage(self, phone, stage):

        self.sessions[phone]["stage"] = stage

    # -----------------------------------
    # Current Item
    # -----------------------------------

    def current_item(self, phone):

        session = self.sessions[phone]

        if session["current"] >= len(session["items"]):

            return None

        return session["items"][session["current"]]

    # -----------------------------------
    # Options
    # -----------------------------------

    def set_options(self, phone, options):

        self.sessions[phone]["options"] = options

    # -----------------------------------
    # Product Selection
    # -----------------------------------

    def select(self, phone, index):

        session = self.sessions[phone]

        product = session["options"][index]

        quantity = session["items"][

            session["current"]

        ]["quantity"]

        variant = product["variations"][0]

        session["selected"].append(

            {

                "displayName": product["displayName"],

                "spinId": variant["spinId"],

                "quantity": quantity,

                "price": variant["price"]["offerPrice"]

            }

        )

        session["current"] += 1

        session["options"] = []

    # -----------------------------------
    # Finished?
    # -----------------------------------

    def finished(self, phone):

        session = self.sessions[phone]

        return session["current"] >= len(session["items"])

    # -----------------------------------
    # Selected Products
    # -----------------------------------

    def selected(self, phone):

        return self.sessions[phone]["selected"]


shopping_session = ShoppingSession()