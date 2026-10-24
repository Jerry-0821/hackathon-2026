# Hackathon 2026 — team rules for Claude

Two-person team. Stack: Python + Streamlit + Claude API. Full plan: `PLAYBOOK.md`.

## Language
- Write everything in this repo in **English**: code, comments, commit messages, docs, UI text. Teammates do not all read Chinese.
- Chatting with a teammate in their own language is fine. Only repo content must be English.
- Use plain, direct English. Short sentences. No buzzwords ("leverage", "delve", "robust", "seamless", "levers"). Say "Test the change", not "It would be advantageous to validate the implementation".

## Git workflow
- Run `git pull` before starting any work.
- Commit small and often. Push after each working feature.
- Before every commit, check that `.env` is not staged. Never commit API keys.
- If `git push` is rejected, run `git pull` first, then push again.
- On a merge conflict, stop and show the teammate both versions. Do not pick one silently.

## Ownership
Fill this in when the idea is decided:
- UI (`app.py`, pages): _name_
- AI logic (`ai.py`, prompts): _name_

Only edit files your teammate owns when asked. Do not rewrite whole files when a small edit works — big rewrites cause merge conflicts.

## Code
- All Claude API calls go in `ai.py`. UI code goes in `app.py`.
- Keep the demo path working at all times. A working small demo beats a broken big one.
- Run `streamlit run app.py` and check the change in the browser before saying it works.
