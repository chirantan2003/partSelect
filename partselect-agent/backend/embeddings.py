# backend/embeddings.py
import asyncio
from google import genai
from google.genai import types
import os

# Initialize Google GenAI client
# It automatically reads GEMINI_API_KEY from environment
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

async def embed(text: str) -> list[float]:
    """Generate 768-dimensional embeddings using gemini-embedding-2."""
    def _call_embed():
        return client.models.embed_content(
            model="gemini-embedding-2",
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
    
    response = await asyncio.to_thread(_call_embed)
    return response.embeddings[0].values
