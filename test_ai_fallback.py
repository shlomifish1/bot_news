import asyncio
import sys
import os

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add the project directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_manager import ai_manager
import config

async def test_fallback():
    print("Starting AI Fallback Test...")
    
    test_prompt = "Say 'Hello, I am working' in Hebrew."
    
    print("\n--- Testing Main Fallback Chain ---")
    result = await ai_manager.chat_completion(test_prompt)
    if result:
        print(f"Success! Response: {result}")
    else:
        print("Error: All models failed.")

    print("\n--- Simulating Fallback (Disabling Groq) ---")
    # Temporarily disable Groq to see if it falls back to Gemini
    original_groq = ai_manager.clients["groq"]
    ai_manager.clients["groq"] = None
    
    result = await ai_manager.chat_completion(test_prompt)
    if result:
        print(f"Success! Response (Fallback): {result}")
    else:
        print("Error: Fallback failed.")
    
    # Restore Groq
    ai_manager.clients["groq"] = original_groq

if __name__ == "__main__":
    asyncio.run(test_fallback())
