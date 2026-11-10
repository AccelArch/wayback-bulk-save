"""record_archive.py: the record it writes, and the optional method.md (--notes)."""
import sys, os, json, shutil, importlib.util, tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "record_archive.py")
results = []
def check(name, cond):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)

spec = importlib.util.spec_from_file_location("record_archive", SRC)
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)

root = tempfile.mkdtemp(prefix="wbs_record_")
site = os.path.join(root, "example_com"); os.makedirs(site)
json.dump([{"url": "https://example.com/a", "ok": True, "ts": 1760000000},
           {"url": "https://example.com/b", "ok": True, "ts": 1760000100},
           {"url": "https://example.com/c", "ok": False, "ts": 1760000200}],
          open(os.path.join(site, "saved_urls_example_com.json"), "w"))
notes = os.path.join(root, "how.md")
open(notes, "w", encoding="utf-8").write("# How the URLs were found\n")
read = lambda p: open(p, encoding="utf-8").read()

# 1) plain record, no notes: no method.md and no link to it
out1 = os.path.join(root, "out1")
folder = ra.write_site_record(ra.build_record(site), out1)
check("saved-urls.txt lists only the confirmed URLs",
      read(os.path.join(folder, "saved-urls.txt")).split() == ["https://example.com/a", "https://example.com/b"])
check("failed-urls.txt lists the failed one", read(os.path.join(folder, "failed-urls.txt")).split() == ["https://example.com/c"])
check("without notes there is no method.md", not os.path.exists(os.path.join(folder, "method.md")))
check("without notes the README does not link method.md", "method.md" not in read(os.path.join(folder, "README.md")))

# 2) with notes: copied as method.md and linked
out2 = os.path.join(root, "out2")
folder = ra.write_site_record(ra.build_record(site), out2, notes)
check("--notes copies the file as method.md", read(os.path.join(folder, "method.md")) == "# How the URLs were found\n")
check("--notes makes the README link method.md", "(method.md)" in read(os.path.join(folder, "README.md")))

# 3) a later run without notes keeps the method.md and keeps linking it
folder = ra.write_site_record(ra.build_record(site), out2)
check("a later run without notes keeps method.md", os.path.isfile(os.path.join(folder, "method.md")))
check("a later run without notes still links method.md", "(method.md)" in read(os.path.join(folder, "README.md")))

# 4) pointing --notes at the method.md that is already in place must not fail or empty it
try:
    ra.write_site_record(ra.build_record(site), out2, os.path.join(out2, "example_com", "method.md"))
    same_ok = read(os.path.join(out2, "example_com", "method.md")) == "# How the URLs were found\n"
except Exception:
    same_ok = False
check("--notes pointing at the file already in place is harmless", same_ok)

shutil.rmtree(root, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
