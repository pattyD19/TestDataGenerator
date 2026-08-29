#!/usr/bin/env python3
"""A stand-in for `adb` backed by a directory on the host.

The emulator loader is the part of `tdg load` most likely to rot silently: it
is a pile of shell strings, and the machine writing it usually has no device
attached. This fake implements the handful of adb verbs the loader uses
against $FAKEADB_ROOT, so push/scan/verify/wipe can be tested in CI with no
emulator and no Android SDK.

It is deliberately not a general adb: anything the loader does not use is an
error, so a new call site shows up as a test failure rather than a silent pass.
"""
import hashlib
import os
import re
import shlex
import shutil
import sys

ROOT = os.environ.get("FAKEADB_ROOT", "/tmp/fakeadb")
SERIAL = os.environ.get("FAKEADB_SERIAL", "emulator-5554")
LOG = os.environ.get("FAKEADB_LOG")
# Set to 1 to imitate the older platform-tools that reject multi-file push,
# which is the case the loader's per-file fallback exists for.
NO_MULTI_PUSH = os.environ.get("FAKEADB_NO_MULTI_PUSH") == "1"


def local(remote):
    """Map a device path into the fake device's root."""
    return os.path.join(ROOT, remote.lstrip("/"))


def log(argv):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(" ".join(argv) + "\n")


def do_shell(args):
    # The loader chains commands with ';' in one invocation to save round trips.
    # Split like a shell would: remote paths are quoted, because an album name
    # contains a space.
    for part in " ".join(args).split(";"):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if not words:
            continue
        cmd, rest = words[0], words[1:]
        if cmd == "mkdir":
            os.makedirs(local(rest[-1]), exist_ok=True)
        elif cmd == "df":
            free_kb = shutil.disk_usage(ROOT).free // 1024
            print("Filesystem     1K-blocks    Used Available Use% Mounted on")
            print(f"/dev/fake      999999999  100000 {free_kb} 1% /sdcard")
        elif cmd == "content":
            # `call ... scan_file` is a no-op here, but the arg is logged so a
            # test can assert every pushed file was actually scanned.
            # `query` is answered for real, so `tdg verify` can be exercised
            # with no emulator: the rows are built from the files this fake
            # actually holds, and datetaken comes from their mtimes — which the
            # generator set to the capture instant.
            if rest[:1] == ["query"]:
                do_query(rest[1:])
        elif cmd == "sha256sum":
            for p in rest:
                lp = local(p)
                if os.path.exists(lp):
                    h = hashlib.sha256(open(lp, "rb").read()).hexdigest()
                    print(f"{h}  {p}")
        elif cmd == "rm":
            for p in [r for r in rest if not r.startswith("-")]:
                try:
                    os.remove(local(p))
                except FileNotFoundError:
                    pass
        elif cmd == "rmdir":
            try:
                os.rmdir(local(rest[0]))
            except OSError:
                return 1
        else:
            sys.stderr.write(f"fakeadb: unsupported shell command {cmd!r}\n")
            return 1
    return 0


def do_query(args):
    """Answer `content query` from the fake device's own filesystem."""
    opts = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    uri = opts.get("uri", "")
    want_video = "/video/" in uri
    m = re.search(r"like\s+'([^%']*)%'", opts.get("where", ""))
    prefix = m.group(1) if m else ""

    rows = []
    for dirpath, _dirs, files in os.walk(ROOT):
        for name in sorted(files):
            if prefix and not name.startswith(prefix):
                continue
            if name.endswith(".mp4") != want_video:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(dirpath, ROOT)
            taken = int(os.path.getmtime(full) * 1000)
            rows.append(f"_display_name={name}, datetaken={taken}, "
                        f"relative_path={rel.replace('sdcard/', '')}/")
    if not rows:
        print("No result found.")
        return
    for i, r in enumerate(rows):
        print(f"Row: {i} {r}")


def do_push(args):
    *srcs, dest = args
    if len(srcs) > 1 and NO_MULTI_PUSH:
        sys.stderr.write("adb: usage: adb push [--sync] LOCAL... REMOTE\n")
        return 1
    ldest = local(dest)
    if dest.endswith("/") or os.path.isdir(ldest):
        os.makedirs(ldest, exist_ok=True)
        for s in srcs:
            shutil.copy2(s, os.path.join(ldest, os.path.basename(s)))
    else:
        os.makedirs(os.path.dirname(ldest), exist_ok=True)
        shutil.copy2(srcs[0], ldest)
    return 0


def main(argv):
    log(argv)
    args = argv[1:]
    if args[:1] == ["-s"]:
        args = args[2:]
    if not args:
        return 1
    verb, rest = args[0], args[1:]
    if verb == "devices":
        print("List of devices attached")
        print(f"{SERIAL}\tdevice product:fake model:Fake_Device")
        return 0
    if verb == "shell":
        return do_shell(rest)
    if verb == "push":
        return do_push(rest)
    sys.stderr.write(f"fakeadb: unsupported verb {verb!r}\n")
    return 1


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    sys.exit(main(sys.argv))
