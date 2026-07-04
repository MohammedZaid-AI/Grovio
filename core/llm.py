import os
from groq import Groq
from openai import OpenAI

from core.config import Config


class LLM:

    def __init__(self):

        if Config.OPENAI_API_KEY:

            self.client = OpenAI(

                api_key=Config.OPENAI_API_KEY,

                base_url=Config.OPENAI_BASE_URL

            )

            self.is_openai = True

        else:

            self.client = Groq(

                api_key=Config.GROQ_API_KEY

            )

            self.is_openai = False

    def chat(

        self,

        system,

        user,

        temperature=None

    ):

        base_url = Config.OPENAI_BASE_URL if getattr(self, "is_openai", False) else "Groq API"
        print(f"\n[DEBUG_LLM_CALL] Model: {Config.MODEL}, BaseURL: {base_url}, Temp: {temperature if temperature is not None else Config.TEMPERATURE}\n")

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