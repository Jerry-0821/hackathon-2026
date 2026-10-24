"""Run `python test_api.py` to check that your API key works."""
import ai

if not ai.has_api_key():
    print("[FAIL] ANTHROPIC_API_KEY not found. Create a .env file first (see .env.example)")
else:
    print(ai.ask("Introduce yourself in one sentence."))
    print("\n[OK] The API works!")
