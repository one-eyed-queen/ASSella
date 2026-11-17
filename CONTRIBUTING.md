# Contributing to EXE-fingerprinter

Thank you for your interest in contributing to EXE-fingerprinter! This document outlines the branch structure, local environment setup, architectural invariants, and contribution standards—including our policy on AI-assisted development.

---

## 1. Branching Strategy & Workflow

EXE-fingerprinter operates on a 4-branch model:

* **`canary` (Bleeding Edge)**: Active development for upcoming features, major refactorings, and experimental additions. **Most new PRs should target this branch.**
* **`beta` (Staging & Preview)**: Stabilization branch for upcoming release candidates. Receives targeted bug fixes and polish.
* **`stable` (Production Releases)**: The current production release. Only critical security or blocker hotfixes are cherry-picked here.
* **`arm64`**: Automated port branch kept in sync with upstream changes.

---

## 2. Local Environment Setup

### Prerequisites

- Linux (SteamOS, Arch, Bazzite, CachyOS, Fedora, Ubuntu, NixOS, etc.) or Windows
- Python 3.10+ with the dependencies in `requirements.txt`
- A terminal (this is a TUI — no Qt)

### Setting Up a Development Clone

```bash
# 1. Clone the repository (check out the canary branch for new features)
git clone -b canary https://github.com/niwia/ASSella.git
cd EXE-fingerprinter

# 2. Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install development dependencies
pip install -r requirements.txt

# 4. Launch EXE-fingerprinter from source
python src/main.py
```

---

## 3. Core Architectural Rules & Invariants

EXE-fingerprinter is built for speed and reliability on the Steam Deck and desktop Linux. All contributions must respect these invariants:

### A. Performance & Responsiveness
- **Fast Startup**: the TUI comes up in under a second. Never do network, disk-wide scans or subprocess work on the render path; use worker threads with a queue, like the TUI does.
- **Lazy Imports**: heavy imports (requests/urllib3, API clients) must happen where they are used, not at module import time.
- **No GUI thread**: the TUI owns stdout/stderr via raw terminal control. Never log to the terminal — logs go to a file (`utils/logger.py`).

### B. Steam & .exe Invariants
- **No Hardcoded Home Paths**: always use `Path.home()` / `os.path.expanduser("~")` / platform data dirs. Never hardcode `/home/deck/`.
- **.exe only**: base-game Windows depots only; DLC depots are skipped. No install/library flows, no `.acf` writing, no `config.yaml` editing.
- **Depot Keys**: local key database → private API → Hubcap. Never embed a Hubcap key in the repo or the client. Never call the private database directly.

### C. Lightweight Repository
- Heavy binary tools (Goldberg, Steamless, SLScheevo under `src/deps/`) are dynamically fetched on demand from R2 and must **never** be committed to git.

---

## 4. AI-Assisted Contributions Policy

EXE-fingerprinter has been developed in close collaboration with AI agents. We welcome AI-assisted contributions (using Gemini, Claude, Copilot, ChatGPT, etc.), subject to the following rules:

1. **Human Accountability**:
   - You, the human contributor, are 100% responsible for the code you submit.
   - Never submit code you do not fully understand. You must be able to explain the architecture, reasoning, and edge-case handling in review.
2. **Context & Invariant Alignment**:
   - Before prompting an AI agent to edit EXE-fingerprinter, provide it with the repository rules in [`.agents/AGENTS.md`](file:///.agents/AGENTS.md).
   - AI tools frequently hallucinate non-existent Qt methods or attempt to generate `.acf` files; you must verify that the AI has followed the repository invariants.
3. **Workspace Hygiene**:
   - Do **NOT** commit AI conversation logs, assistant instructions, transcripts (`.gemini/`, `.claude/`), or local scratch scripts to git.
   - Clean up untracked temporary files before pushing.

---

## 5. Submitting a Pull Request

1. **Create a Topic Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Commit Your Changes**:
   - Write clear, imperative commit messages (e.g. `Fix DLC depot mapping target reset`).
3. **Verify Locally**:
   - Confirm that the application boots without delay and logs no syntax warnings:
     ```bash
     python -X importtime src/main.py
     ```
4. **Open a PR**:
   - Target the appropriate branch (`canary` or `beta`).
   - Fill out the [Pull Request Template](file:///.github/PULL_REQUEST_TEMPLATE.md) completely, verifying all invariant and AI-disclosure checkboxes.

Thank you for helping make EXE-fingerprinter even better!
