#!/usr/bin/env python3
"""Run the tdg control plane.

    python3 packages/web/serve.py --port 8722

Binds 0.0.0.0 by default so devices on the same network can pull packs — the
plan's whole argument for running the generator on the LAN rather than pulling
64 GB from the cloud for every test run.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tdgweb.server import serve     # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8722)
    p.add_argument("--packs", default="./packs", help="where built packs live")
    p.add_argument("--seeds", default=None, help="seed pool (default: repo seed-pool)")
    p.add_argument("--db", default=None, help="job database (default: <packs>/jobs.sqlite3)")
    p.add_argument("--verbose", action="store_true", help="log every request")
    a = p.parse_args()
    serve(a.host, a.port, a.packs, a.seeds, a.db, a.verbose)


if __name__ == "__main__":
    main()
