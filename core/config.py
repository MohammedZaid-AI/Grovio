import os

from dotenv import load_dotenv

load_dotenv()


class Config:

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")

    TEMPERATURE = 0.2

    AUTO_SELECT_CONFIDENCE_THRESHOLD = int(os.getenv("AUTO_SELECT_CONFIDENCE_THRESHOLD", 98))