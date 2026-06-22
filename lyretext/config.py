from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from google import genai
import anthropic

def create_llm(model_type: str = "gemini", **options):
    if model_type == "gemini":
        return create_gemini_llm(**options)
    elif model_type == "anthropic":
        return create_anthropic_llm(**options)
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

def create_gemini_llm(**options) -> ChatGoogleGenerativeAI:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to initialize Gemini.")

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, **options)

def create_anthropic_llm(**options) -> ChatAnthropic:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required to initialize Anthropic.")
    
    return ChatAnthropic(model=model_name, anthropic_api_key=api_key, **options)

def initialise_client(model_type: str = "gemini", **options):
    if model_type == "gemini":
        return initialise_gemini_client(**options)
    elif model_type == "anthropic":
        return initialise_anthropic_client(**options)
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

def initialise_gemini_client(**options):
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to initialize Gemini client.")

    return genai.Client(**options)

def initialise_anthropic_client(**options):
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required to initialize Anthropic client.")

    return anthropic.Client(api_key=api_key, **options).beta