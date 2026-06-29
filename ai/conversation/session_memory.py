from datetime import datetime


class SessionMemory:
    """
    Stores the current conversation state
    for every restaurant.

    Later this can be replaced with Redis.
    """

    def __init__(self):

        self.sessions = {}

    # ---------------------------------
    # Create Session
    # ---------------------------------

    def get(self, restaurant):

        if restaurant not in self.sessions:

            self.sessions[restaurant] = {

                "last_agent": None,

                "last_purchase_order": None,

                "awaiting_approval": False,

                "last_message": None,

                "last_response": None,

                "updated_at": datetime.now()

            }

        return self.sessions[restaurant]

    # ---------------------------------
    # Update
    # ---------------------------------

    def update(

        self,

        restaurant,

        **kwargs

    ):

        session = self.get(restaurant)

        session.update(kwargs)

        session["updated_at"] = datetime.now()

    # ---------------------------------
    # Clear
    # ---------------------------------

    def clear(self, restaurant):

        if restaurant in self.sessions:

            del self.sessions[restaurant]


memory = SessionMemory()


if __name__ == "__main__":

    memory.update(

        "restaurant",

        last_agent="procurement",

        awaiting_approval=True

    )

    from pprint import pprint

    pprint(

        memory.get("restaurant")

    )