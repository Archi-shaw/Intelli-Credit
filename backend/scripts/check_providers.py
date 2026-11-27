"""Classify every external provider honestly, using the server's configuration (.env / environment).

  .venv/bin/python scripts/check_providers.py            # presence only, no network calls
  .venv/bin/python scripts/check_providers.py --live     # one minimal real request per configured provider

Classes: "live-tested OK", "failed — needs attention", "credentials missing (not tested live)",
"local (no external service)". Contract/mocked tests never count as live.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.providers import CHECKABLE, integration_status, live_check  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="make one minimal real request per configured provider")
    args = ap.parse_args()
    st = integration_status()
    print(f"{'provider':<12} {'configured':<11} classification")
    failed = 0
    for name in CHECKABLE:
        entry = st.get(name, {})
        configured = bool(entry.get("configured"))
        if name == "embeddings" and entry.get("provider") == "local-hash":
            cls = "local (no external service)"
        elif not configured:
            cls = "credentials missing (not tested live)"
        elif not args.live:
            cls = "configured, not yet tested (run with --live)"
        else:
            r = live_check(name)
            cls = {"verified": f"live-tested OK ({r['detail']})", "local": "local (no external service)"}.get(
                r["status"], f"failed — needs attention: {r['detail']}")
            failed += r["status"] == "error"
        print(f"{name:<12} {str(configured):<11} {cls}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
