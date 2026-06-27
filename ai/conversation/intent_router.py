from ai.conversation.commands import COMMANDS


class IntentRouter:

    """
    Detects the user's intent before
    invoking LangGraph.
    """

    def detect(self, message: str):

        if not message:

            return "chat"

        message = message.lower().strip()

        # Numeric menu

        if message == "1":

            return "daily_brief"

        if message == "2":

            return "inventory"

        if message == "3":

            return "forecast"

        if message == "4":

            return "report"

        if message == "5":

            return "help"

        # Greetings

        if message in [

            "hi",

            "hello",

            "hey",

            "good morning",

            "good evening",

            "good afternoon"

        ]:

            return "menu"

        # Keyword search

        for intent, keywords in COMMANDS.items():

            for keyword in keywords:

                if keyword in message:

                    return intent

        return "chat"


if __name__ == "__main__":

    router = IntentRouter()

    while True:

        text = input("Message : ")

        print(

            router.detect(text)

        )

        print()