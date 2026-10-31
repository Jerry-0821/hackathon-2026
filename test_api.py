"""Run `python test_api.py` to check that your API key works."""
import os

import anthropic

import agents  # loads .env and holds the MODEL the negotiation uses

if not os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-ant-"):
    print("[FAIL] ANTHROPIC_API_KEY not found. Create a .env file first (see .env.example)")
else:
    try:
        resp = agents._get_client().messages.create(
            model=agents.MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": "Introduce yourself in one sentence."}],
        )
        print(next(b.text for b in resp.content if b.type == "text"))
        print(f"\n[OK] The API works! (model: {agents.MODEL})")
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
