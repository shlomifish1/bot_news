import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, patch
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Use a temporary database for testing
import config
config.DB_FILE = 'test_efficiency.db'

# Import functions to test (we'll mock ai_manager later)
from main import init_db, is_duplicate, clean_or_block_ad, is_semantic_duplicate, main_handler, get_cached_moderation, save_cached_moderation

async def benchmark():
    if os.path.exists(config.DB_FILE):
        os.remove(config.DB_FILE)
    
    init_db()
    
    ai_call_counter = 0

    async def mocked_chat_completion(prompt, temperature=0.7):
        nonlocal ai_call_counter
        ai_call_counter += 1
        # Simple rule-based mock
        if "BLOCK" in prompt or "advertisement" in prompt:
            if "spam" in prompt.lower() or "מבצע" in prompt.lower():
                return "BLOCK"
            return "CLEANED NEWS"
        if "YES" in prompt or "deduplication" in prompt:
            return "NO"
        return "AI RESPONSE"

    with patch('main.ai_manager.chat_completion', new=mocked_chat_completion):
        print("--- Starting Efficiency Benchmark ---")
        
        # Test Case 1: Exact Duplicate
        print("\nTest 1: Identical messages")
        text = "חדשות מתפרצות: איראן שיגרה טילים."
        ai_call_counter = 0
        
        # Simulating main_handler flow
        # In main.py, it's inside main_handler. We'll simulate the calls.
        
        async def process_sim(txt):
             # 1. Local Dup Check (Simhash etc)
             is_dup = is_duplicate(txt, AsyncMock(media=None), 123)
             if is_dup: return "DUPLICATE"
             
             # 2. Moderation (Cache or AI)
             cached = get_cached_moderation(txt)
             if cached: return cached
             
             res = await clean_or_block_ad(txt)
             save_cached_moderation(txt, res)
             return res

        res1 = await process_sim(text)
        print(f"First run result: {res1}, AI calls: {ai_call_counter}")
        
        res2 = await process_sim(text)
        print(f"Second run result: {res2}, AI calls: {ai_call_counter}")
        
        if ai_call_counter == 1:
             print("✅ Success: Identical message used local check/cache!")
        else:
             print(f"❌ Failure: AI called {ai_call_counter} times for identical message.")

        # Test Case 2: Near Duplicate (Simhash)
        print("\nTest 2: Near-identical messages")
        text1 = "צה''ל תקף מטרות בביירות כעת."
        text2 = "צה''ל תקף מטרות בביירות עכשיו! 🚀"
        ai_call_counter = 0
        
        await process_sim(text1)
        print(f"First run (text1), AI calls: {ai_call_counter}")
        
        await process_sim(text2)
        print(f"Second run (text2), AI calls: {ai_call_counter}")
        
        if ai_call_counter == 1:
             print("✅ Success: Near-identical message caught by Simhash before AI!")
        else:
             print(f"❌ Failure: AI called {ai_call_counter} times for near-identical message.")

    print("\n--- Benchmark Complete ---")

if __name__ == "__main__":
    asyncio.run(benchmark())
