import os
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_agents', '.env')
print("Path checked:", dotenv_path)
print("Exists:", os.path.exists(dotenv_path))

load_dotenv(dotenv_path)

print("TOKEN:", os.getenv("TELEGRAM_BOT_TOKEN"))
