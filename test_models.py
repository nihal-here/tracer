import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

for m in client.models.list_models():
    if "embedContent" in m.supported_generation_methods:
        print(m.name)
