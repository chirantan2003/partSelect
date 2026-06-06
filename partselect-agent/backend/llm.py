# backend/llm.py
from langchain_google_genai import ChatGoogleGenerativeAI
import os

# Create Gemini model instances via LangChain
flash = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.environ["GEMINI_API_KEY"],
    temperature=0.2,
)

flash_lite = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash-lite",
    google_api_key=os.environ["GEMINI_API_KEY"],
    temperature=0.0,
)
