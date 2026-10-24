# Hackathon Playbook

Our step-by-step plan for hackathon day. Adapted from a winning workflow, cut down for a two-person Streamlit team.

## Before the event

- [ ] Read the rules: is pre-written code allowed? Theme / tracks? What to submit (repo, video, slides)? Deadline?
- [ ] Both: clone the repo and run `setup.ps1` (see README)
- [ ] Practice one `git pull` / `git push` round together
- [ ] Ask the organizers how API keys / credits are given out, and if we must sign up anywhere first
- [ ] Sign in to [Streamlit Community Cloud](https://share.streamlit.io) with GitHub (for deploying the demo)
- [ ] Collect UI references we like: [Godly](https://godly.website), [Mobbin](https://mobbin.com), [Cosmos](https://cosmos.so)

## Day-of pipeline

### 1. Pick the idea — think backward (~1 hour)
- Imagine we already won. What is the one moment in the demo where judges say "wow"? Start from that.
- Start with the craziest version, then cut it down until it fits in the time we have. Keep the wow moment, drop the rest.
- Check it fits the theme / track and the judging criteria.

### 2. Plan before coding (~1–2 hours)
- Run `/interview <idea>` in Claude Code. Answer its questions until the plan is clear.
- Result: `MASTER_PLAN.md` with the demo script, feature list, cut list and two task tracks.
- Fill in the Ownership section in `CLAUDE.md`.
- Put the API key in `.env` and run `python test_api.py`.

### 3. Build in parallel (most of the time)
- Each person takes one track from `MASTER_PLAN.md` and works with their own Claude.
- Give Claude a reference screenshot for UI work. AI copies a good design far better than it invents one.
- Tell Claude which files it may touch ("only edit app.py").
- Pull before starting, push after each working feature.
- Get the end-to-end demo path working first, then add features.

### 4. Polish (~1 hour)
- Click through the demo like a judge. Fix anything ugly or confusing.
- Ask Claude to review spacing, text, and visual hierarchy against the reference design.
- Deploy to Streamlit Cloud (add `ANTHROPIC_API_KEY` under the app's Secrets). Test the public link.

### 5. Pitch prep (1–2 hours before deadline)
- Write the demo script: problem → our solution → live demo → how it works → what's next.
- Do a real live demo. Record a backup video too (Win + Alt + R) in case the Wi-Fi fails.
- Rehearse at least twice. Stay inside the time limit.
- Be ready to explain the hard technical parts — judges ask.

## Energy
- 24h event: plan to sleep a few hours if the build is on track.
- 36h event: sleep ~5h the first night.
- ~4 AM is the low point. Eat something, take turns resting.

## Tools we decided to skip
- Superset (no Windows support — Claude Desktop already runs parallel sessions)
- Codex, Supabase / Neon / Stripe MCPs (only add if the idea needs a database or payments)
- Animation skills (made for React, not Streamlit)
