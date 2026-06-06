import json
import os

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


def parse_order(user_text):

    with open(
        "prompts/grocery_parser.txt",
        "r",
        encoding="utf-8"
    ) as f:
        system_prompt = f.read()

    completion = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_text
            }
        ]
    )

    response_text = (
        completion
        .choices[0]
        .message
        .content
        .strip()
    )

    print("\nRAW LLM RESPONSE:")
    print(response_text)

    # Remove markdown code blocks if present
    response_text = response_text.strip()

    if response_text.startswith("```"):

        lines = response_text.splitlines()

        # Remove opening ```
        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        # Remove closing ```
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        response_text = "\n".join(
            lines
        ).strip()

    print("\nCLEANED JSON:")
    print(response_text)

    try:

        parsed = json.loads(
            response_text
        )

        print("\nPARSED JSON:")
        print(parsed)

        return parsed

    except Exception as e:

        print("\nJSON ERROR:")
        print(e)

        print("\nFAILED TEXT:")
        print(response_text)

        return {
            "items": [],
            "schedule_time": None,
            "recurrence": None
        }


if __name__ == "__main__":

    text = input(
        "\nEnter grocery request:\n"
    )

    result = parse_order(
        text
    )

    print("\nFINAL RESULT:")
    print(
        json.dumps(
            result,
            indent=2
        )
    )