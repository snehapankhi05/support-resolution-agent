# Setup checklist (Step 0)

Tick each item only after its check command works.

- [ ] **Git + GitHub repo** created (`support-resolution-agent`), first commit pushed. `git --version`
- [ ] **Python 3.11+** `python --version`
- [ ] **uv** installed. `uv --version`
- [ ] **Docker Desktop** running. `docker run hello-world`
- [ ] **Node.js (LTS)** for the MCP Inspector and React. `node --version`
- [ ] **VS Code** with the Python and Docker extensions
- [ ] **LLM API key** (Groq or Gemini free tier) stored in `.env`, never committed.
      Check the provider's *current* rate limits page before relying on it.
- [ ] **Free local embedding model** can be downloaded later (needs ~1 GB disk)
- [ ] `STATUS.md` exists and is up to date
- [ ] `docs/` files read and edited so every decision is **yours**
- [ ] Can explain the project in 2 minutes using `docs/pitch.md` (without reading it)

## Windows notes
- [ ] Docker Desktop uses the WSL2 backend (the installer prompts for it). Reboot if asked.
- [ ] Use PowerShell. `cp .env.example .env` works; or `Copy-Item .env.example .env`
- [ ] If port 5432 is busy (a local Postgres), stop that service or change the mapping
- [ ] Cursor installed and signed in; project folder opened; model selected
- [ ] Cursor > Customize > Rules shows the 5 project rules and AGENTS.md
