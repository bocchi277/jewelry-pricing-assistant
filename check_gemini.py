import os
from google import genai
from dotenv import load_dotenv

# Load the environment variables from your .env file
load_dotenv()

# Initialize the client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

print("Available Models:")
# Loop through the models and print just their names
for model in client.models.list():
    print(f"- {model.name}")