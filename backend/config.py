import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TINYFISH_API_KEY = os.getenv("TINYFISH_API_KEY", "")
TINYFISH_BASE_URL = os.getenv("TINYFISH_BASE_URL", "https://agent.tinyfish.ai")
TINYFISH_CONCURRENCY = int(os.getenv("TINYFISH_CONCURRENCY", "2"))
TINYFISH_BROWSER_PROFILE = os.getenv("TINYFISH_BROWSER_PROFILE", "stealth")
TINYFISH_MAX_SECONDS = int(os.getenv("TINYFISH_MAX_SECONDS", "420"))

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
# "live" = call TinyFish; "replay" = play back recorded runs (no key needed, demo insurance).
# "auto" = live when a TinyFish key is present, else replay.
MODE = os.getenv("GHOST_MODE", "auto")
# Replay speed multiplier: 1.0 = real recorded duration, 20 = 20x faster.
REPLAY_SPEED = float(os.getenv("REPLAY_SPEED", "20"))

DOMAINS_DIR = ROOT / "config" / "domains"
REPLAY_DIR = ROOT / "fixtures" / "replay"
# GHOST_DATA_DIR lets tests and evals run against a throwaway directory.
DATA_DIR = Path(os.getenv("GHOST_DATA_DIR", str(ROOT / "data")))
RECORD_DIR = DATA_DIR / "recorded"
FRONTEND_DIR = ROOT / "frontend"


def effective_mode() -> str:
    """Server default. A mission may still be run in replay on a live server (see app.create_mission)."""
    if MODE == "auto":
        return "live" if TINYFISH_API_KEY else "replay"
    return MODE


def load_domains() -> dict:
    return {p.stem: json.loads(p.read_text()) for p in sorted(DOMAINS_DIR.glob("*.json"))}
