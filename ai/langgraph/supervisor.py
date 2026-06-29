import json
import re

from core.llm import llm
from ai.langgraph.prompts import SUPERVISOR_PROMPT


class Supervisor:
    """
    Grovio Supervisor.

    Responsible for routing the user's message
    to the correct LangGraph agent.
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

        self.dashboard_routes = {

            "dashboard",
            "overview",
            "restaurant overview",
            "status"

        }

        self.history_routes = {

            "history",
            "purchase history",
            "show purchase history",
            "last order",
            "latest order"

        }

    # --------------------------------------------------
    # Purchase Approval
    # --------------------------------------------------

    def is_purchase_approval(self, message):

        approvals = {

            "yes",
            "approve",
            "approve it",
            "go ahead",
            "looks good",
            "confirm",
            "confirm it",
            "place order",
            "place the order",
            "do it",
            "proceed"

        }

        return message in approvals

    # --------------------------------------------------
    # Purchase Rejection
    # --------------------------------------------------

    def is_purchase_rejection(self, message):

        rejections = {

            "no",
            "cancel",
            "cancel it",
            "reject",
            "reject it",
            "decline",
            "don't order",
            "forget it",
            "stop",
            "never mind"

        }

        return message in rejections

    # --------------------------------------------------
    # Purchase Editor
    # --------------------------------------------------

    def is_purchase_editor(self, message):

        if any(

            phrase in message

            for phrase in [

                "show purchase order",
                "show order",
                "show my order",
                "current order",
                "preview order",
                "show"

            ]

        ):

            return True

        if re.search(

            r"(remove|delete)\s+.+",

            message

        ):

            return True

        quantity_patterns = [

            r"(increase|update|set|change)\s+.+\s+to\s+\d+",
            r".+\s+should\s+be\s+\d+"

        ]

        return any(

            re.search(pattern, message)

            for pattern in quantity_patterns

        )

    # --------------------------------------------------
    # Route Message
    # --------------------------------------------------

    def route(self, message):

        message = message.strip().lower()

        if message.startswith("restaurant >"):

            message = message.replace(

                "restaurant >",

                ""

            ).strip()

        # ------------------------
        # Greetings
        # ------------------------

        if message in self.simple_routes:

            return self.simple_routes[message]

        # ------------------------
        # Dashboard
        # ------------------------

        if message in self.dashboard_routes:

            return ["dashboard"]

        # ------------------------
        # Purchase History
        # ------------------------

        if message in self.history_routes:

            return ["purchase_history"]

        # ------------------------
        # Purchase Approval
        # ------------------------

        if self.is_purchase_approval(message):

            return ["purchase_approval"]

        # ------------------------
        # Purchase Rejection
        # ------------------------

        if self.is_purchase_rejection(message):

            return ["purchase_rejection"]

        # ------------------------
        # Purchase Editor
        # ------------------------

        if self.is_purchase_editor(message):

            return ["purchase_editor"]

        # ------------------------
        # Ask the LLM
        # ------------------------

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

            if not isinstance(agents, list):

                return ["coo"]

            if not agents:

                return ["coo"]

            agents = list(dict.fromkeys(agents))

            VALID_AGENTS = {

                "coo",
                "decision",
                "dashboard",
                "procurement",
                "purchase_editor",
                "purchase_approval",
                "purchase_rejection",
                "purchase_history"

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