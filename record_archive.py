#!/usr/bin/env python3
"""
record_archive.py -- write a public, human-readable record of a finished archive.

After wayback_bulk_save.py has saved a site, the site's working folder holds
progress files that are NOT meant to be published (they are git-ignored). This
tool turns them into a clean record inside `archived-sites/`:

    archived-sites/
        README.md                 <- index table of every recorded site
        <site_slug>/
            README.md             <- dates, counts, how to view the snapshots
            saved-urls.txt        <- every URL confirmed saved (one per line)
            failed-urls.txt       <- URLs that never succeeded (only if any)
            info.json             <- the same facts, machine-readable

Usage:
    python record_archive.py <site_folder>
    python record_archive.py thegitcity_com_town_brasil_town
    python record_archive.py <site_folder> --out some/other/folder
    python record_archive.py <site_folder> --exclude /profile/ --exclude /user/

Run it again later and the record is refreshed (for example after a resumed run).
All dates are written in UTC. Uses only the Python standard library.
"""

import os
import sys
import json
import time
import argparse
import urllib.parse


def _utc(ts):
    """Unix timestamp -> 'YYYY-MM-DD HH:MM UTC'."""
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts)) if ts else "-"


def _duration(sec):
    sec = int(max(0, sec))
    days, rest = divmod(sec, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}min")
    return " ".join(parts)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_record(site_folder, excludes=()):
    """Reads a site's working folder and returns the facts about it as a dict.

    `excludes` is a list of text fragments (for example "/profile/"). Any URL that
    contains one of them is counted but NOT written to the public lists."""
    slug = os.path.basename(os.path.abspath(site_folder))
    log_path = os.path.join(site_folder, f"saved_urls_{slug}.json")
    if not os.path.isfile(log_path):
        raise SystemExit(f"Could not find {log_path}\n"
                         f"Pass the folder that wayback_bulk_save.py created for the site.")
    results = _read_json(log_path)

    # For each URL keep: was it EVER confirmed saved, and when was the latest save.
    saved_at = {}   # url -> latest successful timestamp
    tried = set()
    for r in results:
        tried.add(r["url"])
        if r.get("ok"):
            saved_at[r["url"]] = max(saved_at.get(r["url"], 0), r.get("ts", 0))
    failed = sorted(tried - set(saved_at))
    saved = sorted(saved_at)
    stamps = [t for t in saved_at.values() if t]

    # URLs matching an exclusion are counted, but kept out of the published lists.
    def _excluded(url):
        return any(fragment in url for fragment in excludes)
    withheld = sum(1 for u in saved if _excluded(u))
    listed_saved = [u for u in saved if not _excluded(u)]
    listed_failed = [u for u in failed if not _excluded(u)]

    # The timeline file is optional (sites saved before it existed do not have one).
    timeline = None
    tl_path = os.path.join(site_folder, f"timeline_{slug}.json")
    if os.path.isfile(tl_path):
        try:
            timeline = _read_json(tl_path)
        except Exception:
            timeline = None

    site = (timeline or {}).get("site")
    if not site:
        # Fall back to the shortest URL in the log (usually the site's start page).
        site = min(tried, key=len) if tried else slug

    started = (timeline or {}).get("first_start_ts") or (min(stamps) if stamps else None)
    finished = (timeline or {}).get("finished_ts")
    return {
        "slug": slug,
        "site": site,
        "urls_saved": len(saved),
        "urls_failed": len(failed),
        "urls_tried": len(tried),
        "urls_withheld": withheld,
        "excluded_patterns": list(excludes),
        "first_capture_ts": min(stamps) if stamps else None,
        "last_capture_ts": max(stamps) if stamps else None,
        "started_ts": started,
        "finished_ts": finished,
        "recorded_ts": int(time.time()),
        "saved": listed_saved,
        "failed": listed_failed,
    }


def write_site_record(rec, out_root):
    folder = os.path.join(out_root, rec["slug"])
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, "saved-urls.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rec["saved"]) + ("\n" if rec["saved"] else ""))

    failed_path = os.path.join(folder, "failed-urls.txt")
    if rec["failed"]:  # only the URLs that are allowed to be listed
        with open(failed_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(rec["failed"]) + "\n")
    elif os.path.exists(failed_path):
        os.remove(failed_path)  # everything succeeded since the last record

    info = {k: v for k, v in rec.items() if k not in ("saved", "failed")}
    with open(os.path.join(folder, "info.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")

    wayback_view = "https://web.archive.org/web/*/" + rec["site"].rstrip("/") + "/*"
    lines = [
        f"# {rec['site']}",
        "",
        "| | |",
        "|---|---|",
        f"| **Site** | {rec['site']} |",
        f"| **URLs saved** | {rec['urls_saved']} of {rec['urls_tried']} tried |",
    ]
    if rec.get("urls_withheld"):
        patterns = ", ".join(f"`{p}`" for p in rec.get("excluded_patterns", []))
        lines.append(f"| **Not listed here** | {rec['urls_withheld']} saved URLs matching {patterns} "
                     f"(left out on purpose, for privacy) |")
    lines += [
        f"| **First capture** | {_utc(rec['first_capture_ts'])} |",
        f"| **Last capture** | {_utc(rec['last_capture_ts'])} |",
    ]
    if rec["started_ts"]:
        lines.append(f"| **Archiving started** | {_utc(rec['started_ts'])} |")
    if rec["finished_ts"]:
        lines.append(f"| **Archiving finished** | {_utc(rec['finished_ts'])} |")
        if rec["started_ts"]:
            lines.append(f"| **Total time** | {_duration(rec['finished_ts'] - rec['started_ts'])} |")
    lines += [
        f"| **Record last updated** | {_utc(rec['recorded_ts'])} |",
        "",
        f"View the snapshots: <{wayback_view}>",
        "",
        "Files in this folder:",
        "",
        "- [`saved-urls.txt`](saved-urls.txt): every URL confirmed saved, one per line"
        + (" (except the ones noted above as not listed)" if rec.get("urls_withheld") else ""),
    ]
    if rec["failed"]:
        lines.append(f"- [`failed-urls.txt`](failed-urls.txt): {rec['urls_failed']} URL(s) that never succeeded")
    lines += ["- [`info.json`](info.json): the same facts in machine-readable form", ""]
    with open(os.path.join(folder, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return folder


def write_index(out_root):
    """Rebuilds archived-sites/README.md from every <slug>/info.json found."""
    rows = []
    for name in sorted(os.listdir(out_root)):
        info_path = os.path.join(out_root, name, "info.json")
        if os.path.isfile(info_path):
            try:
                rows.append(_read_json(info_path))
            except Exception:
                pass
    rows.sort(key=lambda r: r.get("last_capture_ts") or 0, reverse=True)

    lines = [
        "# Archived sites",
        "",
        "Sites saved to the Wayback Machine with this tool, newest first. "
        "Each folder lists the exact URLs saved and when.",
        "",
        "Want something removed? Removal requests are honored: see "
        "[Removal requests](../README.md#removal-requests).",
        "",
        "| Site | URLs saved | Last capture | Finished |",
        "|---|---|---|---|",
    ]
    for r in rows:
        fin = _utc(r["finished_ts"]) if r.get("finished_ts") else "-"
        lines.append(f"| [{r['site']}]({urllib.parse.quote(r['slug'])}/) | {r['urls_saved']} "
                     f"| {_utc(r.get('last_capture_ts'))} | {fin} |")
    lines.append("")
    with open(os.path.join(out_root, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Write a public record of a finished archive.")
    parser.add_argument("site_folder", help="folder created by wayback_bulk_save.py for the site")
    parser.add_argument("--exclude", action="append", default=[], metavar="TEXT",
                        help="leave out of the public lists every URL that contains TEXT "
                             "(for example /profile/); the URLs are still counted. "
                             "Can be repeated.")
    parser.add_argument("--out", default=None,
                        help="where to write the record (default: archived-sites/ next to this script)")
    args = parser.parse_args()

    out_root = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "archived-sites")
    os.makedirs(out_root, exist_ok=True)

    rec = build_record(args.site_folder, args.exclude)
    folder = write_site_record(rec, out_root)
    write_index(out_root)
    print(f"Recorded {rec['site']}: {rec['urls_saved']} URL(s) saved, "
          f"{rec['urls_failed']} failed.")
    if rec["urls_withheld"]:
        print(f"{rec['urls_withheld']} saved URL(s) were counted but left out of the lists "
              f"(--exclude).")
    print(f"Written to: {folder}")


if __name__ == "__main__":
    sys.exit(main())
