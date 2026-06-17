from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI


def create_gemini_llm(**options) -> ChatGoogleGenerativeAI:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to initialize Gemini.")

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, **options)