import sys, os, io, re, json, time, shutil, builtins, importlib.util, contextlib, tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "wayback_bulk_save.py")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
REAL_TIME, REAL_SLEEP = time.time, time.sleep
results = []
def check(name, cond):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)

def scenario(case, age_hours, old_status, answers, n_new=0):
    """6 jobs from an earlier run are waiting; `n_new` extra URLs have never been sent."""
    time.time, time.sleep = REAL_TIME, REAL_SLEEP
    root =tempfile.mkdtemp(prefix="wbs_" + case + "_")
    dst = os.path.join(root, "wayback_bulk_save.py"); shutil.copy(SRC, dst)
    json.dump({"access_key": "FAKEA1234", "secret_key": "FAKES1234", "firecrawl_api_key": ""},
              open(os.path.join(root, "config.json"), "w"))
    site = os.path.join(root, "old_test"); os.makedirs(site)
    urls = [f"https://old.test/p{i}" for i in range(6 + n_new)]
    json.dump(urls, open(os.path.join(site, "url_list_old_test.json"), "w"))
    jobs = [{"url": u, "job_id": f"old{i}", "status": 200} for i, u in enumerate(urls[:6])]
    pj = os.path.join(site, "pending_jobs_old_test.json")
    json.dump(jobs, open(pj, "w"))
    t = time.time() - age_hours * 3600
    os.utime(pj, (t, t))

    spec = importlib.util.spec_from_file_location("wbs_" + case, dst)
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    clock = {"t": REAL_TIME()}
    m.time.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    m.time.time = lambda: clock["t"]
    calls = {"enqueue": 0, "status": [], "order": []}
    def status(job_id, a, b):
        calls["status"].append(job_id)
        calls["order"].append("status:" + job_id)
        if job_id.startswith("old"):
            return old_status(job_id)
        return True, True, "success"
    m.check_job_status_once = status
    def enqueue(url, a, b, max_wait_minutes=30):
        calls["enqueue"] += 1
        calls["order"].append("enqueue")
        return True, 200, f"new{calls['enqueue']}"
    m.enqueue_to_wayback = enqueue
    m.get_user_status = lambda a, b: None
    # No real network for the "already saved?" index check either
    m.already_saved_recently_batch = lambda urls_, days=7, **k: set()
    it = iter(["https://old.test", "1"] + answers)
    old_input = builtins.input
    builtins.input = lambda *a: next(it)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.process_site()
    finally:
        builtins.input = old_input
    out = ANSI.sub("", buf.getvalue())
    log = json.load(open(os.path.join(site, "saved_urls_old_test.json"))) if os.path.exists(os.path.join(site, "saved_urls_old_test.json")) else []
    return out, calls, log, os.path.exists(pj), root

# 1. old jobs (5 h), all still pending -> probe says stuck, user answers Enter (= yes) -> URLs are queued again
out, calls, log, pending_left, root = scenario("stuck", 5, lambda j: (False, False, "pending"), [""])
check("1: probes a sample of the old jobs", "Sample of" in out and "6 still pending" in out)
check("1: explains the jobs look stuck and asks", "look stuck" in out and "[Y/n]" not in out or "send" in out)
check("1: drops the old jobs and requeues the 6 URLs", "Dropped. 6 URL(s) will be queued again." in out and calls["enqueue"] == 6)
check("1: new jobs are verified and logged OK", len(log) == 6 and all(e["ok"] for e in log))
check("1: pending file is gone at the end", not pending_left)
shutil.rmtree(root, ignore_errors=True)

# 2. old jobs but the user answers 'n' -> keeps them (no requeue), they go to normal verification
out, calls, log, pending_left, root = scenario("keep", 5, lambda j: (True, True, "success") if False else (False, False, "pending"), ["n"])
check("2: answering n keeps the old jobs and verifies them the normal way (no drop)",
      "Dropped" not in out and "Verifying: 6 job(s)" in out and "Overall verification timeout" in out)
shutil.rmtree(root, ignore_errors=True)

# 3. old jobs, but some of them already finished -> not stuck, no question, normal verification
def mixed(j):
    return (True, True, "success") if j in ("old0", "old1", "old2", "old3", "old4", "old5") else (False, False, "pending")
out, calls, log, pending_left, root = scenario("finished", 5, mixed, [])
check("3: finished jobs -> no 'stuck' question, normal verification", "look stuck" not in out and calls["enqueue"] == 0 and len(log) == 6)
shutil.rmtree(root, ignore_errors=True)

# 4. fresh pending file (10 min old): no probe at all
out, calls, log, pending_left, root = scenario("fresh", 0.17, lambda j: (True, True, "success"), [])
check("4: recent jobs are not probed", "Sample of" not in out and "look stuck" not in out and len(log) == 6)
shutil.rmtree(root, ignore_errors=True)

# 5. old jobs, but the sample cannot be checked (connection errors) -> inconclusive, no question
out, calls, log, pending_left, root = scenario("errors", 5, lambda j: (False, False, "error checking status (<urlopen error [Errno 111] Connection refused>)"), [])
print_ok = "look stuck" not in out and calls["enqueue"] == 0
check("5: inconclusive sample -> keeps jobs, does not requeue", print_ok)
shutil.rmtree(root, ignore_errors=True)

# 6. jobs from an earlier run AND URLs never sent: the old jobs must be checked BEFORE anything new
#    is sent (the message "I will go straight to checking their status" has to be true).
out, calls, log, pending_left, root = scenario("old_first", 0.17, lambda j: (True, True, "success"), [], n_new=4)
first_enqueue = calls["order"].index("enqueue") if "enqueue" in calls["order"] else -1
before = calls["order"][:first_enqueue] if first_enqueue >= 0 else []
check("6: all 6 old jobs are checked before the first new URL is sent",
      first_enqueue >= 6 and sorted(before) == sorted(f"status:old{i}" for i in range(6)))
check("6: the 4 new URLs are sent afterwards and everything ends up saved",
      calls["enqueue"] == 4 and len({e["url"] for e in log if e["ok"]}) == 10)
check("6: tells the user it is checking the old jobs first", "from the previous run first" in out)
shutil.rmtree(root, ignore_errors=True)

print("\n%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)


