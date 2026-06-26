import json

from core.llm import llm

from ai.langgraph.prompts import SUPERVISOR_PROMPT


class Supervisor:

    """
    AI Supervisor.

    Uses GPT to determine
    which agents should run.
    """

    def route(self, message):

        response = llm.chat(

            system=SUPERVISOR_PROMPT,

            user=message,

            temperature=0

        )

        try:

            data = json.loads(response)

            return data["agents"]

        except Exception:

            return ["coo"]


if __name__ == "__main__":

    supervisor = Supervisor()

    while True:

        message = input("Restaurant > ")

        print()

        print(

            supervisor.route(message)

        )

        print()