import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

response = client.models.embed_content(
    model="text-embedding-004",
    contents="How does routing work?"
)
print("Embedding length:", len(response.embeddings[0].values))
print("First 3 values:", response.embeddings[0].values[:3])
