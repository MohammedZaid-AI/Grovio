from groq import Groq

from core.config import Config


class LLM:

    def __init__(self):

        self.client = Groq(

            api_key=Config.GROQ_API_KEY

        )

    def chat(

        self,

        system,

        user,

        temperature=None

    ):

        if temperature is None:

            temperature = Config.TEMPERATURE

        response = self.client.chat.completions.create(

            model=Config.MODEL,

            temperature=temperature,

            messages=[

                {

                    "role": "system",

                    "content": system

                },

                {

                    "role": "user",

                    "content": user

                }

            ]

        )

        return response.choices[0].message.content


llm = LLM()