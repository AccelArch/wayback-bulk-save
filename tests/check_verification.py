import os
import sys, io, re, importlib.util, contextlib, urllib.error

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "wayback_bulk_save.py")
def load(name):
    spec = importlib.util.spec_from_file_location(name, SRC)
    mod = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod
m = load("wbs")

ANSI = re.compile(r"\x1b\[[0-9;]*m")
def run(statuses, n, **kw):
    """statuses(i, call_no) -> (resolved, ok, info). Fake clock: sleep() advances time()."""
    clock = {"t": 1000.0, "sleeps": []}
    def fake_sleep(s):
        clock["sleeps"].append(s); clock["t"] += s
    m.time.sleep = fake_sleep
    m.time.time = lambda: clock["t"]
    calls = {"n": 0}
    def chk(job_id, a, b):
        calls["n"] += 1
        return statuses(int(job_id), calls["n"])
    m.check_job_status_once = chk
    items = [{"url": f"https://x.test/{i}", "job_id": str(i)} for i in range(n)]
    got = []; buf = io.StringIO(); err = None; res = None
    with contextlib.redirect_stdout(buf):
        try:
            res = m.verify_all_jobs(items, "k", "s", on_resolved=lambda it, ok, info: got.append((it["url"], ok)), **kw)
        except m.VerificationBlocked as e:
            err = e
    return res, got, ANSI.sub("", buf.getvalue()), clock, err

results = []
def check(name, cond):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)

REFUSED = "error checking status (<urlopen error [Errno 111] Connection refused>)"

# 1. big batch with scattered check errors (never 5 in a row) and jobs still pending at the Archive
def mixed(i, n):
    if i < 100: return True, True, "success"
    if i < 120: return True, False, "error: some failure"
    if 120 <= i < 190 and i % 3 == 0: return False, False, "error checking status (HTTP 429: too many status checks, slow down)"
    return False, False, "pending"
res, got, out, clock, err = run(mixed, 250, max_wait_minutes=30)
check("1: progress line every 25 checks (setting)", "checked 25/250" in out and "checked 100/250" in out)
check("1: warns up front that a pass takes a while", "takes roughly" in out)
check("1: per-URL OK lines are printed", out.count("OK ✓") >= 100)
check("1: pass summary printed", "Pass 1 done: 250 checked -> 100 confirmed, 20 failed, 130 not confirmed yet." in out)
check("1: check errors are shown with reason and count", "x error checking status (HTTP 429" in out)
check("1: no block pause for scattered errors", "Pausing" not in out and err is None)
check("1: timeout uses real elapsed time, after at least 2 passes", "Overall verification timeout" in out and "Pass 2 done" in out)
check("1: callback only for resolved jobs (120)", len(got) == 120)
check("1: unresolved returned as timed out", all(res[str(i)] == (False, "timed out waiting for confirmation") for i in range(120, 250)))

# 2. Connection refused for the first 12 checks (your screenshot), then it recovers
def outage_then_ok(i, n):
    return (False, False, REFUSED) if n <= 12 else (True, True, "success")
res, got, out, clock, err = run(outage_then_ok, 40)
check("2: pauses instead of hammering the Archive", "Pausing 2 min" in out and "Pausing 4 min" in out)
check("2: pauses grow 2 min, 4 min", [s for s in clock["sleeps"] if s >= 120][:2] == [120, 240])
check("2: pass restarted after the pause", "Pass 1 stopped early" in out)
check("2: everything is confirmed at the end", len(got) == 40 and all(ok for _, ok in got))
check("2: nothing marked as failed or timed out", all(v == (True, "success") for v in res.values()))
check("2: the refused connection reason is shown", "Connection refused" in out)

# 3. Permanent outage: gives up cleanly, nothing marked as failed
res, got, out, clock, err = run(lambda i, n: (False, False, REFUSED), 30)
check("3: raises VerificationBlocked after the pauses", isinstance(err, m.VerificationBlocked))
check("3: no job reported as resolved/failed", got == [] and res is None)
big = [s for s in clock["sleeps"] if s >= 120]
check("3: waits 2,4,8,16,30,30 min (total 90 min) and stops", big == [120, 240, 480, 960, 1800, 1800])

# 4. A real answer in between resets the pause counter
def flaky(i, n):
    # 5 failures, then one real answer, repeated: should never give up
    return (False, False, REFUSED) if n % 6 != 0 else (True, True, "success")
res, got, out, clock, err = run(flaky, 12)
check("4: real answers reset the counter (no give-up)", err is None and len(got) == 12)

# 5. all keys rejected -> still reported clearly (blocked after pauses)
res, got, out, clock, err = run(lambda i, n: (False, False, "error checking status (HTTP 401: the Archive rejected your keys)"), 10)
check("5: rejected keys are shown", "rejected your keys" in out and isinstance(err, m.VerificationBlocked))

# 6. small batch, all succeed: unchanged behaviour
res, got, out, clock, err = run(lambda i, n: (True, True, "success"), 5)
check("6: all confirmed, quiet output", len(got) == 5 and "takes roughly" not in out and "Pausing" not in out)

# 7. timeout does not count the time spent waiting out a block
def block_then_pending(i, n):
    if n <= 15: return (False, False, REFUSED)       # 3 pauses = 2+4+8 = 14 min
    return (False, False, "pending")
res, got, out, clock, err = run(block_then_pending, 20, max_wait_minutes=5)
first_timeout_clock = clock["t"] - 1000.0
check("7: pauses are excluded from the timeout clock", "Overall verification timeout" in out and first_timeout_clock > 14 * 60)

# 8. HTTP 404 / 401 / 429 mapping of the single check
class _R: pass
def raising(code):
    def _open(*a, **k): raise urllib.error.HTTPError("u", code, "x", {}, None)
    return _open
m2 = load("wbs2")
m2.urllib.request.urlopen = raising(404); r404 = m2.check_job_status_once("j", "k", "s")
m2.urllib.request.urlopen = raising(401); r401 = m2.check_job_status_once("j", "k", "s")
m2.urllib.request.urlopen = raising(429); r429 = m2.check_job_status_once("j", "k", "s")
check("8: 404 = final failure (job expired)", r404[0] is True and r404[1] is False and "not found" in r404[2])
check("8: 401 = check error mentioning the keys", r401[0] is False and "rejected your keys" in r401[2])
check("8: 429 = check error", r429[0] is False and "429" in r429[2])

print("\n%d/%d passed" % (sum(results), len(results)))
res, got, out, clock, err = run(outage_then_ok, 40)
print("\n--- what the terminal shows during an outage that recovers ---")
print("\n".join([l for l in out.splitlines() if "OK" not in l][:9]))
sys.exit(0 if all(results) else 1)
