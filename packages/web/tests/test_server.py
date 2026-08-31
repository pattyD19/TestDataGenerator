#!/usr/bin/env python3
"""Tests for the control plane.

Runs a real server on a real socket against real builds — no mocking of the
generator, because the interesting failures are at the seams: does the manifest
a loader fetches actually point at files it can fetch, does a cancelled build
really resume, does a Range request return the right bytes.

Kept small and fast by building tiny photo-only packs.

Run it:

    python3 packages/web/tests/test_server.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(WEB))
sys.path.insert(0, WEB)

from tdgweb.server import serve                # noqa: E402
from tdgweb import prune as prune_mod           # noqa: E402

FAILURES = []
BASE = None


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def req(path, method="GET", body=None, headers=None):
    """Returns (status, parsed_or_bytes, headers)."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers=headers or {})
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            parsed = json.loads(raw) if "json" in ctype else raw
            return resp.status, parsed, resp.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), exc.headers
        except json.JSONDecodeError:
            return exc.code, raw, exc.headers


def wait_for(jid, statuses, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        _, job, _ = req(f"/api/jobs/{jid}")
        if job["status"] in statuses:
            return job
        time.sleep(0.3)
    return job


def main():
    global BASE
    tmp = tempfile.mkdtemp(prefix="tdg-web-")
    httpd = serve(host="127.0.0.1", port=0, packs_dir=os.path.join(tmp, "packs"),
                  seeds_dir=os.path.join(REPO, "seed-pool"),
                  db_path=os.path.join(tmp, "jobs.sqlite3"), block=False)
    BASE = f"http://127.0.0.1:{httpd.server_address[1]}"

    print("static and options")
    code, body, hdrs = req("/")
    check(code == 200 and b"<title>" in body, "the UI page is served")
    code, opts, _ = req("/api/options")
    check(code == 200 and opts["presets"] and opts["profiles"], "options lists presets and profiles")
    check(any(p["default"] for p in opts["profiles"]),
          "exactly one profile is marked default so the form matches the API")
    code, _, _ = req("/static/../server.py")
    check(code in (400, 404), "static route refuses a path outside its directory")

    print("validation happens before a two-hour build starts")
    for body, why in [
        ({}, "missing size"),
        ({"size": "nonsense"}, "unparseable size"),
        ({"size": "0MB"}, "zero size"),
        ({"size": "9000GB"}, "absurd size"),
        ({"size": "10MB", "profile": "nokia-3310"}, "unknown profile"),
        ({"size": "10MB", "photo_fraction": 4}, "fraction out of range"),
        ({"size": "10MB", "since": "last tuesday"}, "unparseable date"),
        ({"size": "10MB", "since": "2024-01-01", "until": "2023-01-01"}, "reversed range"),
    ]:
        code, resp, _ = req("/api/jobs", "POST", body)
        if code != 400:
            FAILURES.append(f"accepted {why}")
    check(not [f for f in FAILURES if f.startswith("accepted ")],
          "eight bad requests are all rejected with 400 and a reason")

    print("a real build")
    code, job, _ = req("/api/jobs", "POST", {
        "size": "20MB", "profile": "pixel-8", "photo_fraction": 1.0,
        "label": "test", "since": "2023-01-01", "until": "2024-01-01"})
    check(code == 201 and job["status"] == "running", "job is created and starts running")
    jid, token = job["id"], job["token"]
    check(len(token) == 6 and token.isdigit(), "job gets a six-digit pairing code")

    done = wait_for(jid, ("done", "failed", "cancelled"))
    check(done["status"] == "done",
          f"build finishes (status {done['status']}: {done['message']})")
    if done["status"] != "done":
        print("  build failed, stopping: " + str(done["message"]))
        return 1
    check(done["done_bytes"] == done["target_bytes"],
          "and lands on the exact target through the API too")
    check(done["percent"] == 100.0, "progress reaches 100%")

    print("the pack API a loader consumes")
    code, _, _ = req(f"/api/jobs/{jid}/manifest")
    check(code == 403, "manifest needs the pairing token")
    code, man, _ = req(f"/api/jobs/{jid}/manifest?token={token}")
    check(code == 200 and man["file_count"] == done["file_count"],
          "manifest matches the job record")
    check(all(i.get("url") for i in man["items"]),
          "every item carries a fetchable url, so a loader needs no URL scheme of its own")

    first = man["items"][0]
    code, blob, hdrs = req(first["url"])
    check(code == 200 and len(blob) == first["bytes"], "a pack file downloads at full length")
    import hashlib
    check(hashlib.sha256(blob).hexdigest() == first["sha256"],
          "and its bytes match the manifest checksum")
    check(hdrs.get("Accept-Ranges") == "bytes", "range support is advertised")

    print("range requests, which is how a phone resumes a 4 GB file")
    code, part, hdrs = req(first["url"], headers={"Range": "bytes=0-99"})
    check(code == 206 and len(part) == 100 and part == blob[:100],
          "a byte range returns exactly those bytes")
    code, tailb, _ = req(first["url"], headers={"Range": "bytes=-50"})
    check(code == 206 and tailb == blob[-50:], "a suffix range returns the tail")
    code, mid, _ = req(first["url"], headers={"Range": f"bytes=10-{first['bytes'] + 500}"})
    check(code == 206 and mid == blob[10:], "an over-long range is clamped to the file")
    code, _, _ = req(first["url"], headers={"Range": "bytes=99999999-"})
    check(code == 416, "a range past the end is refused with 416")

    print("refusals")
    code, _, _ = req(f"/api/jobs/{jid}/files/{first['name']}?token=000000")
    check(code == 403, "a wrong token cannot fetch pack bytes")
    code, _, _ = req(f"/api/jobs/{jid}/files/..%2f..%2fjobs.sqlite3?token={token}")
    check(code in (400, 404), "a traversal attempt cannot escape the pack directory")
    code, _, _ = req("/api/jobs/deadbeefdead")
    check(code == 404, "an unknown job is 404, not a crash")
    code, _, _ = req("/api/nope")
    check(code == 404, "an unknown route is 404")

    print("pairing, which is all a phone has to go on")
    code, paired, _ = req(f"/api/pair/{token}")
    check(code == 200 and paired["job_id"] == done["job_id"],
          "a six-digit code alone resolves to the pack")
    check(paired["manifest_url"] == done["manifest_url"],
          "and hands back the manifest URL, so the phone needs no job id")
    check(paired["total_bytes"] == done["done_bytes"] and
          paired["file_count"] == done["file_count"],
          "with the totals a device checks against its own free space")
    code, _, _ = req("/api/pair/000000")
    check(code == 404, "an unknown code is refused")
    code, _, _ = req("/api/pair/nonsense")
    check(code == 404, "a non-numeric code is refused rather than matched loosely")

    print("cancel and resume")
    code, big, _ = req("/api/jobs", "POST", {
        "size": "160MB", "profile": "pixel-8", "photo_fraction": 1.0,
        "since": "2023-01-01", "until": "2024-01-01"})
    bid = big["id"]
    # Wait for real progress so the cancel lands mid-build rather than before it.
    end = time.time() + 120
    while time.time() < end:
        _, j, _ = req(f"/api/jobs/{bid}")
        if j["done_bytes"] > 0 or j["status"] != "running":
            break
        time.sleep(0.3)
    check(j["status"] == "running" and j["done_bytes"] > 0,
          f"the big build is genuinely under way ({j['file_count']} files)")

    code, resp, _ = req(f"/api/jobs/{bid}/cancel", "POST")
    check(code == 200 and resp["cancelled"], "cancel reports success")
    stopped = wait_for(bid, ("cancelled", "failed", "done"))
    check(stopped["status"] == "cancelled", f"job goes to cancelled (got {stopped['status']})")
    partial = stopped["done_bytes"]
    check(0 < partial < stopped["target_bytes"], "and keeps the work it had already done")

    code, _, _ = req(f"/api/jobs/{bid}/resume", "POST")
    check(code == 200, "resume is accepted")
    finished = wait_for(bid, ("done", "failed"))
    check(finished["status"] == "done", f"resumed build finishes (got {finished['status']})")
    check(finished["done_bytes"] == finished["target_bytes"],
          "and still lands exactly on target after being interrupted")
    check(finished["done_bytes"] > partial, "having built on what the cancel preserved")

    code, _, _ = req(f"/api/jobs/{jid}/resume", "POST")
    check(code == 409, "a finished job cannot be resumed")

    print("listing")
    code, jobs, _ = req("/api/jobs")
    check(code == 200 and len(jobs) >= 2, "jobs are listed newest first")
    check(jobs[0]["created_at"] >= jobs[-1]["created_at"], "ordering is newest first")

    print("prune: the survey")
    code, survey, _ = req("/api/prune")
    check(code == 200 and survey["eligible"] >= 2,
          "the survey lists the packs that can be reclaimed")
    check(survey["reclaimable_bytes"] > 0 and survey["reclaimable_human"],
          f"and reports what they would free ({survey.get('reclaimable_human')})")
    row = next(r for r in survey["jobs"] if r["id"] == jid)
    check(row["present"] and row["bytes"] >= done["done_bytes"],
          "a pack measures at least its manifest total, since LICENSES.csv counts too")

    print("prune: the guards")
    outside = os.path.join(tmp, "not-a-pack")
    os.makedirs(outside, exist_ok=True)
    rogue = httpd.store.create("rogue1", {"size": "1MB"}, 1, outside)
    httpd.store.update(rogue["id"], status="done")
    try:
        prune_mod.prune_job(httpd.store, httpd.packs_dir, rogue["id"])
        refused = False
    except prune_mod.PruneError as exc:
        refused = exc.code == 400
    check(refused and os.path.isdir(outside),
          "a pack_dir outside the packs root is refused, and that directory survives")
    httpd.store.delete(rogue["id"])

    partial_dir = os.path.join(httpd.packs_dir, "partial1")
    os.makedirs(partial_dir, exist_ok=True)
    for name in prune_mod.CHECKPOINT:
        with open(os.path.join(partial_dir, name), "w") as fh:
            fh.write("{}\n")
    part = httpd.store.create("partial1", {"size": "1MB"}, 1, partial_dir)
    httpd.store.update(part["id"], status="cancelled")
    code, resp, _ = req(f"/api/jobs/{part['id']}/prune", "POST")
    check(code == 409 and "partial" in resp["error"],
          "an unfinished build is not pruned by accident")
    check(os.path.isdir(partial_dir), "and its checkpoint is left where resume needs it")
    code, survey2, _ = req("/api/prune")
    prow = next(r for r in survey2["jobs"] if r["id"] == part["id"])
    check(prow["resumable"] and not prow["eligible"],
          "the survey marks it resumable rather than reclaimable")
    code, _, _ = req(f"/api/jobs/{part['id']}/prune?force=1", "POST")
    check(code == 200 and not os.path.isdir(partial_dir),
          "force discards it, which has to be asked for explicitly")

    print("prune: one pack")
    pack_dir = row["pack_dir"]
    code, out, _ = req(f"/api/jobs/{jid}/prune", "POST")
    check(code == 200 and out["freed_bytes"] > 0,
          f"pruning a finished pack frees its bytes ({out.get('freed_human')})")
    check(not os.path.isdir(pack_dir), "the pack directory is gone from disk")
    _, pruned, _ = req(f"/api/jobs/{jid}")
    check(pruned["status"] == "pruned", "and the job stays in the list, marked pruned")

    code, resp, _ = req(f"/api/jobs/{jid}/manifest?token={token}")
    check(code == 410 and "prune" in resp.get("error", ""),
          "its manifest answers 410 Gone and says why, not 'not built yet'")
    code, resp, _ = req(first["url"])
    # This is the route a device is actually on when a pack is pruned mid-fill,
    # and it must not claim the file was never in the pack. Regression: the
    # allowlist is built from the manifest, which the prune deletes, so this
    # answered 404 "not in this pack" until the status was checked first.
    check(code == 410 and "pruned" in resp.get("error", ""),
          "its files answer 410 with the reason too, not 'not in this pack'")
    code, resp, _ = req(f"/api/pair/{token}")
    check(code == 410 and "pruned" in resp.get("error", ""),
          "and its code answers 410 with the reason, so a loader can say which "
          "of 'wrong code' and 'pack is gone' it is")
    code, resp, _ = req("/api/pair/000001")
    check(code == 404 and "no pack" in resp.get("error", ""),
          "while an unknown code stays a plain 404")
    building = httpd.store.create("building1", {"size": "1MB"}, 1,
                                  os.path.join(httpd.packs_dir, "building1"))
    httpd.store.update(building["id"], status="running")
    code, resp, _ = req(f"/api/pair/{building['token']}")
    check(code == 409 and "still building" in resp.get("error", ""),
          "and a code for a build still running says so rather than 'not found'")
    httpd.store.delete(building["id"])

    print("prune: rebuild, because a pack is reproducible")
    code, _, _ = req(f"/api/jobs/{jid}/resume", "POST")
    check(code == 200, "a pruned job can be built again")
    rebuilt = wait_for(jid, ("done", "failed"))
    check(rebuilt["status"] == "done" and
          rebuilt["done_bytes"] == rebuilt["target_bytes"],
          "and the rebuild lands on the same exact target")
    code, man2, _ = req(f"/api/jobs/{jid}/manifest?token={token}")
    check(code == 200 and [i["sha256"] for i in man2["items"]] ==
          [i["sha256"] for i in man["items"]],
          "with byte-identical contents — pruning costs build time, not information")

    print("prune: in bulk, and deleting the row")
    partial2 = os.path.join(httpd.packs_dir, "partial2")
    os.makedirs(partial2, exist_ok=True)
    for name in prune_mod.CHECKPOINT:
        with open(os.path.join(partial2, name), "w") as fh:
            fh.write("{}\n")
    p2 = httpd.store.create("partial2", {"size": "1MB"}, 1, partial2)
    httpd.store.update(p2["id"], status="cancelled")

    code, bulk, _ = req("/api/prune", "POST", {})
    check(code == 200 and bulk["freed_bytes"] > 0,
          f"a bulk prune reclaims the rest ({bulk.get('freed_human')})")
    check(os.path.isdir(partial2) and
          any(sk["id"] == p2["id"] for sk in bulk["skipped"]),
          "while passing over an unfinished build, and saying which it skipped")
    check(all(not os.path.isdir(os.path.join(httpd.packs_dir, r["job_id"]))
              for r in bulk["pruned"]), "every pack it reported is really gone")
    _, survey3, _ = req("/api/prune")
    check(survey3["eligible"] == 0 and survey3["reclaimable_bytes"] == 0,
          "leaving nothing to reclaim")
    _, still, _ = req("/api/jobs")
    check(len(still) >= 2, "the job history survives the packs")

    code, out, _ = req(f"/api/jobs/{jid}", "DELETE")
    check(code == 200 and out["row"] == "deleted", "delete removes the row as well")
    code, _, _ = req(f"/api/jobs/{jid}")
    check(code == 404, "after which the job is gone from the API")
    code, _, _ = req("/api/jobs/deadbeefdead/prune", "POST")
    check(code == 404, "pruning an unknown job is 404, not a crash")

    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
