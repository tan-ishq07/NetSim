"""Upload tutor model artifacts to the public Backblaze B2 bucket.

Files land under ``netsim-tutor/<version>/...`` and (when --latest) are also
mirrored to ``netsim-tutor/latest/...`` so the app can always fetch a stable
URL. Credentials come from the environment (never committed):

    B2_KEY_ID, B2_APP_KEY   (required)
    B2_BUCKET               (default: netsim-models)

Usage:
    python ai/scripts/upload_artifacts.py --src /path/to/artifacts --version v2026.06.19 --latest
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from b2sdk.v2 import B2Api, InMemoryAccountInfo


def _api() -> B2Api:
    key_id = os.environ.get("B2_KEY_ID")
    app_key = os.environ.get("B2_APP_KEY")
    if not key_id or not app_key:
        sys.exit("Set B2_KEY_ID and B2_APP_KEY in the environment.")
    api = B2Api(InMemoryAccountInfo())
    api.authorize_account("production", key_id, app_key)
    return api


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="directory of artifacts to upload")
    ap.add_argument("--version", required=True)
    ap.add_argument("--bucket", default=os.environ.get("B2_BUCKET", "netsim-models"))
    ap.add_argument("--prefix", default="netsim-tutor")
    ap.add_argument("--latest", action="store_true", help="also mirror to /latest")
    args = ap.parse_args()

    api = _api()
    bucket = api.get_bucket_by_name(args.bucket)
    src = Path(args.src)
    files = [p for p in src.rglob("*") if p.is_file()]
    if not files:
        sys.exit(f"no files under {src}")

    dests = [args.version] + (["latest"] if args.latest else [])
    for p in files:
        rel = p.relative_to(src).as_posix()
        for d in dests:
            key = f"{args.prefix}/{d}/{rel}"
            print(f"upload {p}  ->  {args.bucket}/{key}  ({p.stat().st_size:,} B)")
            bucket.upload_local_file(local_file=str(p), file_name=key)
    print("done.")


if __name__ == "__main__":
    main()
