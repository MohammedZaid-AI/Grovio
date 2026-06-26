import json

from core.llm import llm
from ai.langgraph.prompts import SUPERVISOR_PROMPT


class Supervisor:
    """
    Grovio Supervisor.

    Decides which internal agent(s)
    should handle the user's request.
    """

    def __init__(self):

        self.simple_routes = {

            "hi": ["coo"],
            "hello": ["coo"],
            "hey": ["coo"],
            "good morning": ["coo"],
            "good afternoon": ["coo"],
            "good evening": ["coo"]

        }

    # ---------------------------------------
    # Route Message
    # ---------------------------------------

    def route(self, message):

        message = message.strip().lower()

        # -------------------------------
        # Fast deterministic routing
        # -------------------------------

        if message in self.simple_routes:

            return self.simple_routes[message]

        # -------------------------------
        # Ask the LLM
        # -------------------------------

        response = llm.chat(

            system=SUPERVISOR_PROMPT,

            user=message,

            temperature=0

        )

        print("\n========== SUPERVISOR ==========")
        print("Message :", message)
        print("LLM :", response)
        print("================================\n")

        try:

            data = json.loads(response)

            agents = data.get("agents", [])

            # Ensure list

            if not isinstance(agents, list):

                return ["coo"]

            # Never allow empty routing

            if len(agents) == 0:

                return ["coo"]

            # Remove duplicates

            agents = list(dict.fromkeys(agents))

            # Only allow valid agents

            valid_agents = []

            for agent in agents:

                if agent in [

                    "coo",

                    "decision"

                ]:

                    valid_agents.append(agent)

            if not valid_agents:

                return ["coo"]

            return valid_agents

        except Exception as e:

            print("Supervisor Error:", e)

            return ["coo"]


if __name__ == "__main__":

    supervisor = Supervisor()

    while True:

        message = input("Restaurant > ")

        if message.lower() == "exit":

            break

        print()

        print(

            supervisor.route(message)

        )

        print()