import json

from core.llm import llm
from ai.langgraph.prompts import SUPERVISOR_PROMPT


class Supervisor:
    """
    Grovio Supervisor.

    Responsible for routing every user
    message to the correct AI agent.

    All routing decisions are made by the LLM.
    """

    def __init__(self):

        pass

    # --------------------------------------------------
    # Route Message
    # --------------------------------------------------

    def route(self, message):

        message = message.strip()

        if message.lower().startswith("restaurant >"):

            message = message.replace(

                "Restaurant >",

                ""

            ).replace(

                "restaurant >",

                ""

            ).strip()

        response = llm.chat(

            system=SUPERVISOR_PROMPT,

            user=message,

            temperature=0

        )

        print()

        print("=" * 50)

        print("SUPERVISOR")

        print("Message :", message)

        print("LLM :", response)

        print("=" * 50)

        print()

        try:

            import re
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            data = json.loads(clean_response)

            agents = data.get("agents", [])

            if not isinstance(agents, list):

                return ["coo"]

            VALID_AGENTS = {

                "coo",

                "decision",

                "dashboard",

                "procurement",

                "purchase_editor",

                "purchase_approval",

                "purchase_rejection",

                "purchase_history",

                "auto_order",

                "receive_order"

            }

            agents = [

                agent

                for agent in dict.fromkeys(agents)

                if agent in VALID_AGENTS

            ]

            if not agents:

                return ["coo"]

            return agents

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