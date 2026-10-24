# Hackathon 2026

Python + Streamlit + Claude API

## Files

| File | What it does |
|---|---|
| `app.py` | Web UI (Streamlit) |
| `ai.py` | Claude API logic. Change `SYSTEM_PROMPT` here |
| `test_api.py` | Checks that your API key works |
| `setup.ps1` | One-click first-time setup |
| `.env` | Your API key (**never uploaded to GitHub**, everyone creates their own) |
| `CLAUDE.md` | Team rules that Claude reads automatically |
| `PLAYBOOK.md` | Our hackathon plan, step by step |

## First-time setup (once per computer)

Install [Git](https://git-scm.com) and [Python 3.10+](https://python.org) first (tick "Add python.exe to PATH"). Then:

```powershell
git clone https://github.com/Jerry-0821/hackathon-2026.git
cd hackathon-2026
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` creates the virtual environment, installs packages and creates `.env`. Then open `.env` and paste the API key after `ANTHROPIC_API_KEY=`.

> If `Activate.ps1` says "running scripts is disabled", run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Every time you start work

```powershell
.venv\Scripts\Activate.ps1
python test_api.py      # check the key (optional)
streamlit run app.py    # open the web app
```

Save your code, then click **Rerun** at the top right of the web page to see changes.

## Working together

- Before you start: `git pull`. When done: `git add .` → `git commit -m "what you did"` → `git push`
- Edit different files where possible: one person owns `app.py` (UI), the other owns `ai.py` (AI logic)
- Send the API key by private message. **Never put it in code.**

## Changing the model

`MODEL` in `ai.py`:

- `claude-opus-5-5`: best quality (default)
- `claude-sonnet-5-5`: about half the price
- `claude-haiku-5-5`: cheapest and fastest
