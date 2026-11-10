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

# 5) card.svg: the skyline picture
import argparse
import xml.etree.ElementTree as ET
SVG = "{http://www.w3.org/2000/svg}"
OFF = "#2a2a30"

def windows(svg_text):
    """Fill colors of the windows (the only 4x4 rectangles in the picture)."""
    return [r.get("fill") for r in ET.fromstring(svg_text).iter(SVG + "rect")
            if r.get("width") == "4" and r.get("height") == "4"]

rec = ra.build_record(site)                       # 2 saved of 3 tried, no url_list file
svg = ra.build_card_svg(rec)
w = windows(svg)
check("card.svg is valid XML", ET.fromstring(svg).tag == SVG + "svg")
check("card says '2 of 3 pages saved'", "2 of 3 pages saved" in svg)
check("lit windows match the share of pages saved (2 of 3)", sum(c != OFF for c in w) == round(len(w) * 2 / 3))
check("the same site always gets the same skyline", ra.build_card_svg(rec) == svg)
check("another site gets another skyline", ra.build_card_svg(dict(rec, slug="other_site")) != svg)
check("the record writes card.svg and the README shows it",
      os.path.isfile(os.path.join(out2, "example_com", "card.svg"))
      and "](card.svg)" in read(os.path.join(out2, "example_com", "README.md")))

# the total comes from the list the run was given, not only from the URLs tried so far
site2 = os.path.join(root, "big_site"); os.makedirs(site2)
json.dump([{"url": f"https://big.test/{i}", "ok": True, "ts": 1760000000 + i} for i in range(3)],
          open(os.path.join(site2, "saved_urls_big_site.json"), "w"))
json.dump([f"https://big.test/{i}" for i in range(6)], open(os.path.join(site2, "url_list_big_site.json"), "w"))
rec2 = ra.build_record(site2)
w2 = windows(ra.build_card_svg(rec2))
check("the total is taken from the URL list (3 saved of 6)", rec2["urls_total"] == 6 and "3 of 6 pages saved" in ra.build_card_svg(rec2))
check("lit windows follow it (half)", sum(c != OFF for c in w2) == round(len(w2) * 0.5))
check("nothing saved: every window is dark", all(c == OFF for c in windows(ra.build_card_svg(dict(rec2, urls_saved=0)))))
check("everything saved: every window is lit", all(c != OFF for c in windows(ra.build_card_svg(dict(rec2, urls_saved=6)))))

# colors
check("--color changes the accent", "#ff0066" in ra.build_card_svg(rec, "#ff0066") and "#c8e64a" not in ra.build_card_svg(rec, "#ff0066"))
check("a color without # is accepted", ra._hex_color("C8E64A") == "#c8e64a")
try:
    ra._hex_color("green"); bad_color_refused = False
except argparse.ArgumentTypeError:
    bad_color_refused = True
check("a color that is not #rrggbb is refused", bad_color_refused)

# odd site names
weird = ra.build_card_svg(dict(rec, site="https://a&b<c>.com/x"))
check("special characters in the site name are escaped (still valid XML)",
      ET.fromstring(weird) is not None and "a&amp;b&lt;c&gt;.com/x" in weird)
longname = ra.build_card_svg(dict(rec, site="https://" + "x" * 70 + ".com"))
sizes = [int(t.get("font-size")) for t in ET.fromstring(longname).iter(SVG + "text") if t.get("font-weight") == "700"]
check("a very long site name is cut and its text shrinks to fit", sizes and 10 <= sizes[0] < 20 and "…" in longname)

# --no-card removes the picture and the README stops showing it
ra.write_site_record(ra.build_record(site), out2, card=False)
check("without the card, card.svg is removed and the README does not show it",
      not os.path.exists(os.path.join(out2, "example_com", "card.svg"))
      and "card.svg" not in read(os.path.join(out2, "example_com", "README.md")))

shutil.rmtree(root, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
