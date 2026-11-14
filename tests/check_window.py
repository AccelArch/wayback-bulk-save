import sys, os, io, re, json, shutil, builtins, importlib.util, contextlib, tempfile, time

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "wayback_bulk_save.py")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
REAL_TIME, REAL_SLEEP = time.time, time.sleep
results = []

def check(name, cond):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name, flush=True)

def scenario(case, n_urls, status_fn, window=10, reap_every=5, reap_checks=8, user_status=None, enqueue_fn=None):
    """status_fn(job, url, now, submitted_at, attempt_no) -> (resolved, ok, info). Fake clock."""
    time.time, time.sleep = REAL_TIME, REAL_SLEEP
    root = tempfile.mkdtemp(prefix="wbsw_" + case + "_")
    dst = os.path.join(root, "wayback_bulk_save.py"); shutil.copy(SRC, dst)
    json.dump({"access_key": "FAKEA1234", "secret_key": "FAKES1234", "firecrawl_api_key": ""},
              open(os.path.join(root, "config.json"), "w"))
    site = os.path.join(root, "new_test"); os.makedirs(site)
    urls = [f"https://new.test/p{i}" for i in range(n_urls)]
    json.dump(urls, open(os.path.join(site, "url_list_new_test.json"), "w"))

    spec = importlib.util.spec_from_file_location("wbsw_" + case, dst)
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    clock = {"t": REAL_TIME()}
    m.time.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    m.time.time = lambda: clock["t"]
    m.SPN_MAX_IN_FLIGHT, m.SPN_REAP_EVERY, m.SPN_REAP_CHECKS = window, reap_every, reap_checks

    st = {"enq": 0, "url_of": {}, "at": {}, "attempts": {}, "done": set(), "max_inflight": 0, "enq_urls": []}
    def enqueue(url, a, b, max_wait_minutes=30):
        st["enq"] += 1; st["enq_urls"].append(url)
        if enqueue_fn:
            refused = enqueue_fn(url, st["enq"])      # None = accept as usual, else (status, detail) = refuse
            if refused: return False, refused[0], refused[1]
        job = f"job{st['enq']}"; st["url_of"][job] = url; st["at"][job] = clock["t"]
        st["attempts"][url] = st["attempts"].get(url, 0) + 1
        st["max_inflight"] = max(st["max_inflight"], len(st["url_of"]) - len(st["done"]))
        return True, 200, job
    def status(job, a, b):
        res = status_fn(job, st["url_of"][job], clock["t"], st["at"][job], st["attempts"][st["url_of"][job]])
        if res[0]: st["done"].add(job)
        return res
    m.enqueue_to_wayback = enqueue
    m.check_job_status_once = status
    m.get_user_status = lambda a, b: user_status(clock["t"]) if callable(user_status) else user_status
    m.already_saved_recently_batch = lambda urls_, days=7, **k: set()
    m.already_saved_recently = lambda url, days=1, **k: False

    it = iter(["https://new.test", "1"])
    old_input = builtins.input
    builtins.input = lambda *a: next(it)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.process_site()
    finally:
        builtins.input = old_input
    out = ANSI.sub("", buf.getvalue())
    logp = os.path.join(site, "saved_urls_new_test.json")
    log = json.load(open(logp)) if os.path.exists(logp) else []
    pj = os.path.join(site, "pending_jobs_new_test.json")
    pending_left = json.load(open(pj)) if os.path.exists(pj) else []
    return out, st, log, pending_left, root, clock

# A. Healthy Archive: every job finishes at once
out, st, log, pend, root, clock = scenario("fast", 40, lambda j, u, now, at, n: (True, True, "success"))
check("A: all 40 URLs saved and logged OK", len({e['url'] for e in log if e['ok']}) == 40)
check("A: nothing was sent twice", st["enq"] == 40)
check("A: never more than the window waiting", st["max_inflight"] <= 10)
check("A: says 'accepted, not saved yet' (not a confirmation)", "accepted by the Archive, not saved yet" in out)
check("A: confirmations appear while still sending", out.index("confirmed OK") < out.rindex("Queueing:"))
check("A: ends cleanly, no pending file", pend == [] and "Finished: 40/40" in out)
shutil.rmtree(root, ignore_errors=True)

# B. Slow but moving: each job needs 20 minutes. The script must wait, not resend, not give up.
slow = lambda j, u, now, at, n: (True, True, "success") if now >= at + 1200 else (False, False, "pending")
out, st, log, pend, root, clock = scenario("slow", 30, slow)
check("B: slow Archive -> all 30 URLs end up saved", len({e['url'] for e in log if e['ok']}) == 30)
check("B: nothing resent while the Archive was just slow", st["enq"] == 30)
check("B: window respected", st["max_inflight"] <= 10)
check("B: tells the user the Archive is behind", "the Archive is behind" in out)
check("B: no stall abort", "has not finished any" not in out)
shutil.rmtree(root, ignore_errors=True)

# C. Stalled Archive: jobs are accepted and never finish
never = lambda j, u, now, at, n: (False, False, "pending")
out, st, log, pend, root, clock = scenario("stalled", 30, never)
check("C: stops with the 'paused' message after the stall time", "is not finishing them" in out and "none finished for 45 minutes" in out)
check("C: sent only a window's worth, not all 30", st["enq"] == 10)
check("C: waiting jobs are kept for the next run", len(pend) == 10)
check("C: nothing marked as failed", not [e for e in log if not e["ok"]])
check("C: took about the stall time of (fake) waiting", 45 * 60 <= (clock["t"] - REAL_TIME()) <= 120 * 60)
shutil.rmtree(root, ignore_errors=True)

# D. One job ends in an error while still sending: it is sent again at the end and then saved
def flaky(j, u, now, at, n):
    if u.endswith("/p3") and n == 1: return True, False, "error: target server error (HTTP status=504)"
    return True, True, "success"
out, st, log, pend, root, clock = scenario("retry", 12, flaky)
check("D: the failed URL was sent a second time", st["attempts"]["https://new.test/p3"] == 2 and st["enq"] == 13)
p3 = [e for e in log if e["url"].endswith("/p3")]
check("D: log shows the failure and then the success", [e["ok"] for e in p3] == [False, True])
check("D: every URL ends up saved", len({e['url'] for e in log if e['ok']}) == 12)
shutil.rmtree(root, ignore_errors=True)

# E. The Archive says something about a pending job (e.g. it is busy): show it once
busy = lambda j, u, now, at, n: (True, True, "success") if now >= at + 600 else (False, False, "pending: Archive is busy, about 21 min")
out, st, log, pend, root, clock = scenario("busy", 20, busy)
check("E: the Archive's message is shown", "Archive says: Archive is busy, about 21 min" in out)
check("E: shown only once", out.count("Archive says:") == 1)
check("E: still finishes all 20", len({e['url'] for e in log if e['ok']}) == 20)
shutil.rmtree(root, ignore_errors=True)


# F. Stalled AND the Archive says processing = 0: stop after ~10 minutes, not 45
out, st, log, pend, root, clock = scenario("f", 30, never, user_status={"processing": 0, "available": 3, "daily_captures": 91, "daily_captures_limit": 30000})
check("F: stops because processing = 0", "not processing any of your requests right now (processing = 0)" in out)
check("F: stops after about 10 minutes, not 45", 10 * 60 <= (clock["t"] - REAL_TIME()) < 20 * 60)
check("F: jobs kept, nothing failed", len(pend) == 10 and not [e for e in log if not e["ok"]])
shutil.rmtree(root, ignore_errors=True)

# G. Slow but WORKING (processing > 0): it keeps waiting past the 10 minute check and finishes
slow2 = lambda j, u, now, at, n: (True, True, "success") if now >= at + 1500 else (False, False, "pending")
out, st, log, pend, root, clock = scenario("g", 30, slow2, user_status={"processing": 3, "available": 0, "daily_captures": 91, "daily_captures_limit": 30000})
check("G: working but slow -> says it is just slow and waits", "just slow, waiting" in out)
check("G: no abort and everything saved", "is not finishing them" not in out and len({e['url'] for e in log if e['ok']}) == 30)
check("G: nothing resent", st["enq"] == 30)
shutil.rmtree(root, ignore_errors=True)

# H. Limits are shown at the start, with a warning when today's allowance is short
out, st, log, pend, root, clock = scenario("h", 12, lambda j, u, now, at, n: (True, True, "success"),
    user_status={"processing": 1, "available": 2, "daily_captures": 29995, "daily_captures_limit": 30000, "daily_status": 10, "daily_status_limit": 70000})
check("H: shows the account line", "Archive account: 1 of 3 capture slots busy" in out and "captures today 29995/30000" in out)
check("H: warns that only 5 captures are left today", "Only 5 captures are left" in out)
shutil.rmtree(root, ignore_errors=True)

# I. Jobs that fail only in the FINAL verification (not while sending) must still be sent again.
#    (Regression test: the retry step once looked at a list that had already been emptied.)
def fail_first_time(j, u, now, at, n):
    return (True, False, "error: target server error (HTTP status=504)") if n == 1 else (True, True, "success")
out, st, log, pend, root, clock = scenario("final_retry", 8, fail_first_time, window=100, reap_every=100)
check("I: failures found in the final verification are sent again", st["enq"] == 16)
check("I: every URL ends up saved", len({e['url'] for e in log if e['ok']}) == 8)
shutil.rmtree(root, ignore_errors=True)
# J. The progress line must count only the jobs that were really saved as "confirmed", and report the
#    failed ones separately. (Regression test: it once counted every finished job as confirmed.)
def two_fail_first_time(j, u, now, at, n):
    # the script sends in alphabetical order (p0, p1, p10, p11, p2...), so p0 and p1 are in the first 5
    if u.endswith(("/p0", "/p1")) and n == 1: return True, False, "error: target server error (HTTP status=504)"
    return True, True, "success"
out, st, log, pend, root, clock = scenario("progress_count", 12, two_fail_first_time)
lines = re.findall(r"(\d+) confirmed in this run(?:, (\d+) failed)?", out)
check("J: the first progress line counts 3 confirmed and 2 failed (5 jobs finished)", lines and lines[0] == ("3", "2"))
check("J: the two failures are also shown one by one", out.count("FAILED ✗") == 2)
check("J: a line without any failure has no 'failed' text", "confirmed in this run, 0 failed" not in out)
shutil.rmtree(root, ignore_errors=True)
out, st, log, pend, root, clock = scenario("progress_clean", 12, lambda j, u, now, at, n: (True, True, "success"))
clean = re.findall(r"\d+ confirmed in this run(, \d+ failed)?", out)
check("J: with no failures the line says only 'confirmed'", clean and all(g == "" for g in clean))
shutil.rmtree(root, ignore_errors=True)
# K. The Archive answers 401 to every new request (keys rejected, or this connection blocked): stop after a few,
#    keep what was accepted, and do NOT write the refused URLs to the log as failures. (It once kept going
#    through the whole list and marked every URL as failed.)
NOT_LOGGED_IN = (401, '{"message":"You need to be logged in to use Save Page Now."}')
out, st, log, pend, root, clock = scenario("auth_block", 30, lambda j, u, now, at, n: (False, False, "pending"),
    window=100, reap_every=100, enqueue_fn=lambda url, n: NOT_LOGGED_IN if n > 6 else None)
check("K: stops after 4 refusals in a row instead of trying all 30", st["enq"] == 6 + 4)
check("K: explains what happened and what to do", "Stopping so nothing else is sent" in out and "archive.org/account/s3.php" in out)
check("K: the refused URLs are NOT logged as failures", not [e for e in log if not e["ok"]])
check("K: the 6 accepted jobs are kept for the next run", len(pend) == 6)
check("K: does not go on to verify or retry", "--- Verifying" not in out and "Retrying" not in out)
tl = json.load(open(os.path.join(root, "new_test", "timeline_new_test.json")))
check("K: the run is recorded as paused (not finished)", tl["runs"][-1]["status"] == "paused_auth_block" and not tl.get("finished_ts"))
shutil.rmtree(root, ignore_errors=True)
# an isolated 401 between good answers is still an ordinary failure, logged as before
out, st, log, pend, root, clock = scenario("auth_isolated", 12, lambda j, u, now, at, n: (True, True, "success"),
    enqueue_fn=lambda url, n: NOT_LOGGED_IN if n in (3, 4) else None)
bad = [e for e in log if not e["ok"]]
check("K: two refusals in a row (below the limit) are logged as failures and the run goes on", len(bad) == 2 and "Stopping so nothing else is sent" not in out)
shutil.rmtree(root, ignore_errors=True)
print("\n%d/%d passed" %(sum(results), len(results)))
sys.exit(0 if all(results) else 1)

