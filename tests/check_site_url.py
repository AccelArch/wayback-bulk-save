"""The site address prompt must refuse empty or invalid answers.

An empty answer used to be accepted: the script then wrote its progress files, with no site
name in them, straight into its own folder. Runs on a copy of the script in a temp folder."""
import sys, os, io, re, shutil, builtins, importlib.util, contextlib, tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "wayback_bulk_save.py")
results = []
def check(name, cond):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)

root = tempfile.mkdtemp(prefix="wbs_siteurl_")
dst = os.path.join(root, "wayback_bulk_save.py"); shutil.copy(SRC, dst)
spec = importlib.util.spec_from_file_location("wbs_siteurl", dst)
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)
m.HAS_BS4 = True
m.keys_are_set = lambda: True


class Stop(Exception):
    pass


def ask_site(answers):
    """Feeds the answers to the prompt; returns (times the address was asked, refusals shown,
    whether the script moved on to the next question)."""
    feed, asked = iter(answers), []
    def fake_input(prompt=""):
        asked.append(prompt)
        try:
            return next(feed)
        except StopIteration:
            raise Stop()  # no more answers: the script is waiting at its next question
    real_input = builtins.input
    builtins.input = fake_input
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m._process_site_inner()
    except Stop:
        pass
    finally:
        builtins.input = real_input
    out = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
    site_asks = sum("site URL" in p for p in asked)
    moved_on = any("How should the pages be mapped" in p for p in asked)
    return site_asks, out.count("does not look like a site address"), moved_on


# 1) empty, a stray letter, a text with spaces: all refused; then a valid one is accepted
asks, refused, moved = ask_site(["", "n", "my site", "example.com"])
check("empty / one letter / text with spaces are each refused (3 refusals)", refused == 3)
check("the address is asked again after each refusal (4 asks)", asks == 4)
check("the valid address is accepted and the script moves on to the next question", moved)

# 2) valid forms are accepted at the first try
for ok in ("example.com", "https://example.com", "https://example.com/section", "www.example.com/a/b"):
    asks, refused, moved = ask_site([ok])
    check(f"'{ok}' is accepted at once", asks == 1 and refused == 0 and moved)

# 3) nothing is created in the script's folder while the address is still invalid
ask_site(["", "n"])
leftovers = [f for f in os.listdir(root) if f not in ("wayback_bulk_save.py", "__pycache__")]
check("no files or folders are created while the address is invalid", leftovers == [])

shutil.rmtree(root, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
