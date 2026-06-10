import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# Initialize the client (picks up GEMINI_API_KEY from your environment)
client = genai.Client(api_key=api_key)

def translate_chunk_to_pretext(source_text: str, source_format: str) -> str:
    system_prompt = (
        "You are an expert technical document translator. Your task is to convert "
        f"raw {source_format} into valid, well-formed PreTeXt XML. Ensure that all "
        "math markdown maps to <m> or <me> tags, and structure is strictly preserved."
    )
    
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"), # Use Pro if the math/structure requires heavy reasoning
        contents=f"Translate the following snippet:\n\n{source_text}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1, # Low temperature for strict, deterministic translations
        )
    )
    
    return response.text

# Example usage
raw_markdown = "### Section 1\n\nLet $x$ be a real number where $$x^2 = 4$$."
pretext_xml = translate_chunk_to_pretext(raw_markdown, "markdown")
print(pretext_xml)