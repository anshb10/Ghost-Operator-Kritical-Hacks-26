"""Every test runs against a throwaway data directory and never touches the network (replay mode only)."""
import json
import os
import tempfile

os.environ["GHOST_DATA_DIR"] = tempfile.mkdtemp(prefix="ghost-test-")
os.environ["GHOST_MODE"] = "replay"
os.environ["REPLAY_SPEED"] = "100000"
os.environ["TINYFISH_API_KEY"] = ""  # belt and braces: no live calls even if .env has a key
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402

from backend import config  # noqa: E402

config.TINYFISH_API_KEY = ""
config.MODE = "replay"
config.REPLAY_SPEED = 100000


@pytest.fixture
def domains():
    return config.load_domains()


def load_offers(domain: str, vendors: list[tuple[str, str]]):
    from backend.ml import normalize
    cfg = config.load_domains()[domain]
    offers = []
    for key, name in vendors:
        rec = json.loads((config.REPLAY_DIR / domain / f"{key}.json").read_text())
        offers += normalize.normalize_offers(name, rec["result"], cfg)
    return offers


def load_pages(domain: str) -> dict:
    data = json.loads((config.REPLAY_DIR / domain / "_fetch.json").read_text())
    from backend.tinyfish import provenance, recorded_date
    return {u: {**p, "fetched_at": recorded_date(data["_source"]), "provenance": provenance(data["_source"])}
            for u, p in data["pages"].items()}
