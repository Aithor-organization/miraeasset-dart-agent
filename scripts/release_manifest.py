#!/usr/bin/env python3
"""코드·프롬프트·평가·인덱스의 재현성 manifest를 생성한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_MANIFEST = ROOT / "eval" / "baseline" / "v1.0.0" / "manifest.json"
PROMPT_FILES = (
    ROOT / "src" / "dart_agent" / "agent" / "narrate.py",
    ROOT / "src" / "dart_agent" / "agent" / "route_section.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "release-manifest.json")
    parser.add_argument("--index", type=Path, default=ROOT / "index" / "dart.sqlite")
    parser.add_argument("--response-model", default=None)
    args = parser.parse_args()

    baseline = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))
    goldset = BASELINE_MANIFEST.parent / baseline["file"]
    index_path = args.index.resolve()
    source_files = sorted((ROOT / "src").rglob("*.py"))
    source_hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in source_files}
    source_digest = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    data = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {
            "commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
        },
        "model": {
            "requested": os.environ.get("CLOVA_CHAT_MODEL", "HCX-007"),
            "response": args.response_model,
            "llm_enabled": os.environ.get("LLM_ENABLED", "1").lower()
            not in ("0", "false", "off"),
        },
        "baseline": {
            "version": baseline["version"],
            "count": baseline["count"],
            "manifest_sha256": sha256(BASELINE_MANIFEST),
            "goldset_sha256": sha256(goldset),
        },
        "source": {
            "file_count": len(source_hashes),
            "tree_sha256": source_digest,
        },
        "prompts": {
            str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p)
            for p in PROMPT_FILES
        },
        "index": {
            "path": str(index_path),
            "present": index_path.is_file(),
            "sha256": sha256(index_path) if index_path.is_file() else None,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
