import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "eval" / "baseline" / "v1.0.0" / "manifest.json"


def test_frozen_baseline_manifest_matches_file():
    meta = json.loads(MANIFEST.read_text(encoding="utf-8"))
    goldset = MANIFEST.parent / meta["file"]
    raw = goldset.read_bytes()
    rows = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert meta["status"] == "frozen"
    assert len(rows) == meta["count"] == 186
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]
