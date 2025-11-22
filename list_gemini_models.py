import os
from dotenv import load_dotenv
from google import genai

load_dotenv()  # loads GEMINI_API_KEY from .env

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is not set")

client = genai.Client(api_key=api_key)

print("Available models:\n")
for m in client.models.list():
    # m.name will look like "models/gemini-2.0-flash-001"
    print(m.name)
