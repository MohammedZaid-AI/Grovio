import json
import re
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

        # Remove accidental console prompt if pasted
        if message.startswith("restaurant >"):
            message = message.replace("restaurant >", "").strip()
        # -------------------------------
        # Fast deterministic routing
        # -------------------------------

        if message in self.simple_routes:

            return self.simple_routes[message]
        

        if message in {

            "no",

            "cancel",

            "reject",

            "decline"

        }:

            return ["purchase_rejection"]
        

        if message == "show":

            return ["purchase_editor"]

        if message.startswith("remove"):

            return ["purchase_editor"]

        if re.search(r"increase .+ to \d+", message):

            return ["purchase_editor"]

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

            VALID_AGENTS = {

                "coo",

                "decision",

                "procurement",

                "purchase_approval"

            }

            valid_agents = [

                agent

                for agent in agents

                if agent in VALID_AGENTS

            ]

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