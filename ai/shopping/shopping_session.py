class ShoppingSession:

    """
    Stores the shopping conversation state.
    """

    def __init__(self):

        self.sessions = {}

    def start(self, phone, items):

        self.sessions[phone] = {

            "items": items,

            "current": 0,

            "selected": [],

            "options": []

        }

    def get(self, phone):

        return self.sessions.get(phone)

    def current_item(self, phone):

        session = self.sessions[phone]

        if session["current"] >= len(session["items"]):

            return None

        return session["items"][session["current"]]

    def set_options(

        self,

        phone,

        options

    ):

        self.sessions[phone]["options"] = options

    def select(

        self,

        phone,

        index

    ):

        session = self.sessions[phone]

        product = session["options"][index]

        quantity = session["items"][

            session["current"]

        ]["quantity"]

        variant = product["variations"][0]

        session["selected"].append(

            {

                "displayName":

                    product["displayName"],

                "spinId":

                    variant["spinId"],

                "quantity":

                    quantity,

                "price":

                    variant["price"]["offerPrice"]

            }

        )

        session["current"] += 1

        session["options"] = []

    def finished(self, phone):

        session = self.sessions[phone]

        return session["current"] >= len(

            session["items"]

        )

    def end(self, phone):

        return self.sessions.pop(

            phone,

            None

        )


shopping_session = ShoppingSession()