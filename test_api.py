"""Run `python test_api.py` to check that your API key works."""
import anthropic

import ai

if not ai.has_api_key():
    print("[FAIL] ANTHROPIC_API_KEY not found. Create a .env file first (see .env.example)")
else:
    try:
        print(ai.ask("Introduce yourself in one sentence."))
        print("\n[OK] The API works!")
    except anthropic.AuthenticationError:
        print("[FAIL] The key is invalid. Copy it again from console.anthropic.com")
    except anthropic.APIStatusError as e:
        if "credit" in str(e.message).lower():
            print("[OK] The key is valid, but the account has no credits yet.")
            print("     Setup is fine. Add credits (or use the hackathon key) to get real replies.")
        else:
            print(f"[FAIL] API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        print("[FAIL] Can't reach Claude. Check your internet connection.")
