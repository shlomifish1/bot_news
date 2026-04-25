import os
import logging
import asyncio
import httpx
from google import genai
from openai import AsyncOpenAI
import config

logger = logging.getLogger("AIManager")

# ─── AI Gateway URL (ai_agents server running locally) ───────────────────────
# כשהשרת הראשי פעיל, bot_news יפנה אליו במקום לנהל AI עצמאי.
# זה מאפשר ניצול FREE_CASCADE המשותף ומעקב עלויות מרכזי.
AI_GATEWAY_URL = os.environ.get("AI_GATEWAY_URL", "http://127.0.0.1:8000/api/ai/complete")
AI_GATEWAY_TIMEOUT = 12  # שניות

class AIManager:
    def __init__(self):
        self.clients = {
            "groq": None,
            "gemini": None,
            "hf": None
        }
        self._init_clients()

    def _init_clients(self):
        groq_key = getattr(config, "GROQ_API_KEY", "") or ""
        gemini_key = getattr(config, "GEMINI_API_KEY", "") or ""
        hf_token = getattr(config, "HF_TOKEN", "") or ""

        # Initialize Groq client
        if groq_key:
            try:
                self.clients["groq"] = AsyncOpenAI(
                    api_key=groq_key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0
                )
            except Exception as e:
                logger.error(f"Failed to init Groq client: {e}")

        # Initialize Gemini client
        if gemini_key:
            try:
                self.clients["gemini"] = genai.Client(api_key=gemini_key)
            except Exception as e:
                logger.error(f"Failed to init Gemini client: {e}")

        # Initialize Hugging Face client
        if hf_token:
            try:
                self.clients["hf"] = AsyncOpenAI(
                    base_url="https://router.huggingface.co/v1/",
                    api_key=hf_token,
                    max_retries=0
                )
            except Exception as e:
                logger.error(f"Failed to init HF client: {e}")

    async def _try_gateway(self, prompt: str, temperature: float = 0.7) -> str | None:
        """
        🔀 מנסה קודם את ה-AI Gateway של ai_agents (http://localhost:8000/api/ai/complete).
        אם השרת הראשי פעיל — כל הבקשות עוברות דרכו וזה מאחד את ה-FREE_CASCADE.
        """
        try:
            async with httpx.AsyncClient(timeout=AI_GATEWAY_TIMEOUT) as client:
                resp = await client.post(
                    AI_GATEWAY_URL,
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "source": "bot_news",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and data.get("result"):
                        logger.info(f"✅ Gateway OK → model: {data.get('model', '?')}")
                        return data["result"]
        except Exception as e:
            logger.debug(f"AI Gateway unavailable (fallback to local): {e}")
        return None

    async def chat_completion(self, prompt, temperature=0.7):
        """
        סדר עדיפות:
          1. AI Gateway (ai_agents server) — אם פעיל, מנצל FREE_CASCADE המשותף
          2. Fallback מקומי: Groq → Gemini → HuggingFace
        """
        # 1. Try unified gateway first
        gateway_result = await self._try_gateway(prompt, temperature)
        if gateway_result:
            return gateway_result

        # 2. Local fallback cascade
        logger.info("ℹ️ Gateway לא זמין — fallback ל-cascade מקומי")
        for model_key in getattr(config, "FALLBACK_ORDER", []):
            model_info = getattr(config, "MODELS", {}).get(model_key)
            if not model_info:
                continue
            
            provider, model_name = model_info
            client = self.clients.get(provider)
            if not client:
                continue

            print(f"Trying AI model: {model_key} ({model_name})...")

            try:
                if provider == "gemini":
                    # Use Gemini SDK (already async compatible with aio)
                    response = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    return response.text.strip()
                elif provider in ["groq", "hf"]:
                    # Use OpenAI-compatible Async SDK
                    messages = [{"role": "user", "content": prompt}]
                    response = await client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=1500
                    )
                    return response.choices[0].message.content.strip()
            except Exception as e:
                print(f"Error: {model_key} failed: {e}")
                logger.error(f"AI Model {model_key} failed: {e}")
                continue

        return None

# Singleton
ai_manager = AIManager()
