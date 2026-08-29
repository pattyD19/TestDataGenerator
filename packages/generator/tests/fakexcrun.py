#!/usr/bin/env python3
"""A stand-in for `xcrun` covering the simctl verbs the loader uses.

Same reasoning as fakeadb.py: the simulator loader is the primary CI target on
macOS, but simctl needs full Xcode, so on a machine with only the Command Line
Tools the code path would otherwise never execute. This fake keeps a JSON
device list and a directory standing in for the Photos library, so addmedia,
device resolution and erase are all exercised.
"""
import json
import os
import shutil
import sys

ROOT = os.environ.get("FAKEXCRUN_ROOT", "/tmp/fakexcrun")
LOG = os.environ.get("FAKEXCRUN_LOG")
# "booted", "none" or "two" — device resolution has a different answer for each.
MODE = os.environ.get("FAKEXCRUN_MODE", "booted")

UDID_A = "11111111-1111-1111-1111-111111111111"
UDID_B = "22222222-2222-2222-2222-222222222222"


def photos_dir(udid):
    return os.path.join(ROOT, udid, "Photos")


def device_list():
    runtime = "com.apple.CoreSimulator.SimRuntime.iOS-17-5"
    devices = []
    if MODE in ("booted", "two"):
        devices.append({"udid": UDID_A, "name": "iPhone 15 Pro", "state": "Booted"})
    if MODE == "two":
        devices.append({"udid": UDID_B, "name": "iPhone SE", "state": "Booted"})
    return {"devices": {runtime: devices}}


def main(argv):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(" ".join(argv) + "\n")
    args = argv[1:]

    if args[:1] == ["--find"]:
        # The loader uses this to tell full Xcode from Command Line Tools.
        print(f"/fake/usr/bin/{args[1]}")
        return 0
    if args[:1] != ["simctl"]:
        sys.stderr.write(f"xcrun: error: unable to find utility {args[0]!r}\n")
        return 72

    verb, rest = args[1], args[2:]
    if verb == "list":
        print(json.dumps(device_list()))
        return 0
    if verb == "addmedia":
        udid, paths = rest[0], rest[1:]
        if udid not in (UDID_A, UDID_B):
            sys.stderr.write(f"Invalid device: {udid}\n")
            return 164
        dest = photos_dir(udid)
        os.makedirs(dest, exist_ok=True)
        for p in paths:
            if not os.path.exists(p):
                sys.stderr.write(f"An error was encountered processing {p}\n")
                return 1
            shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
        return 0
    if verb in ("shutdown", "boot"):
        return 0
    if verb == "erase":
        shutil.rmtree(os.path.join(ROOT, rest[0]), ignore_errors=True)
        return 0
    sys.stderr.write(f"fakexcrun: unsupported simctl verb {verb!r}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
