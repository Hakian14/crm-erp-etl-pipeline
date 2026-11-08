"""Entry point: python run_pipeline.py [--no-db] [--quiet]"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from etl.pipeline import run  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true", help="skip the SQLite load")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    os.makedirs("outputs", exist_ok=True)
    handlers = [logging.FileHandler("outputs/pipeline_run.log", mode="w", encoding="utf-8")]
    if not args.quiet:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", handlers=handlers)
    run(load_db=not args.no_db)
