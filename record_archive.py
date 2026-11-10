#!/usr/bin/env python3
# Copyright (c) 2026 AccelArch
# SPDX-License-Identifier: MIT
# https://github.com/AccelArch/wayback-bulk-save
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
            card.svg              <- a small picture: a pixel-art skyline whose lit windows
                                     are the pages saved so far
            method.md             <- how the URL list was obtained (only with --notes)

Usage:
    python record_archive.py <site_folder>
    python record_archive.py example_com
    python record_archive.py <site_folder> --out some/other/folder
    python record_archive.py <site_folder> --exclude /profile/ --exclude /user/
    python record_archive.py <site_folder> --notes how-the-urls-were-found.md
    python record_archive.py <site_folder> --color "#c8e64a"   # accent color of card.svg
    python record_archive.py <site_folder> --no-card

Run it again later and the record is refreshed (for example after a resumed run).
All dates are written in UTC. Uses only the Python standard library.
"""

import os
import re
import sys
import json
import time
import html
import zlib
import random
import shutil
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

    # How many URLs the run has to save in total (the list it was given). While a run is still
    # going, this is larger than the number of URLs tried so far.
    total = len(tried)
    list_path = os.path.join(site_folder, f"url_list_{slug}.json")
    if os.path.isfile(list_path):
        try:
            listed = _read_json(list_path)
            if isinstance(listed, dict):
                listed = listed.get("urls", [])
            total = max(total, len(listed))
        except Exception:
            pass

    started = (timeline or {}).get("first_start_ts") or (min(stamps) if stamps else None)
    finished = (timeline or {}).get("finished_ts")
    return {
        "slug": slug,
        "site": site,
        "urls_saved": len(saved),
        "urls_failed": len(failed),
        "urls_tried": len(tried),
        "urls_total": total,
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


DEFAULT_ACCENT = "#c8e64a"


def _hex_color(text):
    """argparse type: '#c8e64a' or 'c8e64a' -> '#c8e64a'."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"not a color like #c8e64a: {text!r}")
    return "#" + m.group(1).lower()


def _mix(color, target, amount):
    """Moves `color` toward `target` (both '#rrggbb') by `amount` (0..1)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(target[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def build_card_svg(rec, accent=DEFAULT_ACCENT):
    """A small pixel-art skyline. Every window is one page of the list: it is lit when the page
    is confirmed saved, dark while it is still waiting. The skyline is drawn from the site's
    slug, so a site always gets the same one."""
    W, H, GROUND = 800, 240, 204
    total = rec.get("urls_total") or rec["urls_tried"]
    saved = rec["urls_saved"]
    progress = min(1.0, saved / total) if total else 0.0
    rng = random.Random(zlib.crc32(rec["slug"].encode("utf-8")))

    BG0, BG1, BG2, OFF, TEXT, MUTED = "#0d0d0f", "#141417", "#1c1c20", "#2a2a30", "#ececef", "#9a9aa3"
    dark = _mix(accent, "#000000", 0.30)
    horizon = _mix(accent, "#000000", 0.80)
    lit_colors = [accent, accent, _mix(accent, "#000000", 0.10), _mix(accent, "#ffffff", 0.18)]
    font = "ui-monospace,Menlo,Consolas,monospace"

    buildings, x = [], 0
    while x < W:
        w = rng.choice([24, 28, 32, 36, 40, 44])
        h = rng.choice([48, 64, 80, 96, 112, 128, 148])
        shade = rng.choice([BG1, BG2, "#1a1b1f"])
        antenna = h >= 112 and rng.random() < 0.5
        buildings.append((x, w, h, shade, antenna))
        x += w + rng.choice([0, 4, 4, 8])
    windows = [(wx, wy)
               for bx, bw, bh, _s, _a in buildings
               for wy in range(GROUND - bh + 8, GROUND - 8, 10)
               for wx in range(bx + 6, bx + bw - 8, 9)]
    lit = set(rng.sample(range(len(windows)), round(len(windows) * progress)))

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'role="img" aria-label="Pixel-art skyline: lit windows are pages saved to the Wayback Machine">',
         f'<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG0}"/>'
         f'<stop offset="0.62" stop-color="{BG1}"/><stop offset="1" stop-color="{horizon}"/></linearGradient></defs>',
         f'<rect width="{W}" height="{H}" fill="url(#sky)"/>']
    for _ in range(34):
        sx, sy = rng.randrange(440, W - 8, 4), rng.randrange(8, 70, 4)
        s.append(f'<rect x="{sx}" y="{sy}" width="2" height="2" fill="{_mix(accent, "#ffffff", 0.6)}" '
                 f'opacity="{rng.choice([0.35, 0.55, 0.8])}"/>')
    for bx, bw, bh, shade, antenna in buildings:
        top = GROUND - bh
        s.append(f'<rect x="{bx}" y="{top}" width="{bw}" height="{bh}" fill="{shade}"/>')
        s.append(f'<rect x="{bx}" y="{top}" width="{bw}" height="2" fill="{dark}" opacity="0.35"/>')
        if antenna:
            s.append(f'<rect x="{bx + bw // 2 - 1}" y="{top - 12}" width="2" height="12" fill="{shade}"/>')
            s.append(f'<rect x="{bx + bw // 2 - 2}" y="{top - 14}" width="4" height="3" fill="{accent}"/>')
    for i, (wx, wy) in enumerate(windows):
        fill = rng.choice(lit_colors) if i in lit else OFF
        s.append(f'<rect x="{wx}" y="{wy}" width="4" height="4" fill="{fill}"/>')

    s.append(f'<rect x="0" y="{GROUND}" width="{W}" height="{H - GROUND}" fill="{BG0}"/>')
    s.append(f'<rect x="0" y="{GROUND + 12}" width="{W}" height="16" fill="{BG2}"/>')
    for dx in range(8, W, 40):
        s.append(f'<rect x="{dx}" y="{GROUND + 19}" width="20" height="2" fill="{accent}" opacity="0.85"/>')

    name = re.sub(r"^https?://", "", rec["site"]).rstrip("/")
    if len(name) > 56:
        name = name[:55] + "…"
    size = max(10, min(20, int(372 / (0.6 * len(name))))) if name else 20
    last = time.strftime("%Y-%m-%d", time.gmtime(rec["last_capture_ts"])) if rec.get("last_capture_ts") else ""
    credit = "Wayback Bulk Save" + (f" · {last}" if last else "")
    s += [f'<rect x="20" y="20" width="410" height="92" fill="{BG0}" opacity="0.72"/>',
          f'<rect x="20" y="20" width="3" height="92" fill="{accent}"/>',
          f'<text x="38" y="50" font-family="{font}" font-size="{size}" font-weight="700" fill="{accent}">'
          f'{html.escape(name)}</text>',
          f'<text x="38" y="76" font-family="{font}" font-size="14" fill="{TEXT}">'
          f'{saved:,} of {total:,} pages saved</text>',
          f'<text x="38" y="98" font-family="{font}" font-size="12" fill="{MUTED}">{html.escape(credit)}</text>',
          f'<rect x="552" y="20" width="228" height="48" fill="{BG0}" opacity="0.72"/>',
          f'<rect x="566" y="31" width="6" height="6" fill="{accent}"/>',
          f'<text x="580" y="38" font-family="{font}" font-size="12" fill="{TEXT}">lit window = page saved</text>',
          f'<rect x="566" y="50" width="6" height="6" fill="{OFF}"/>',
          f'<text x="580" y="57" font-family="{font}" font-size="12" fill="{TEXT}">dark window = still waiting</text>',
          '</svg>']
    return "\n".join(s) + "\n"


def write_site_record(rec, out_root, notes_path=None, card=True, accent=DEFAULT_ACCENT):
    """Writes the record. `notes_path` is an optional text file (for example, how the list of
    URLs was obtained); it is copied into the record as method.md. A method.md that is already
    in the folder is kept and linked. `card` writes card.svg, the skyline picture."""
    folder = os.path.join(out_root, rec["slug"])
    os.makedirs(folder, exist_ok=True)

    card_path = os.path.join(folder, "card.svg")
    if card:
        with open(card_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(build_card_svg(rec, accent))
    elif os.path.exists(card_path):
        os.remove(card_path)

    method_path = os.path.join(folder, "method.md")
    if notes_path and os.path.abspath(notes_path) != os.path.abspath(method_path):
        shutil.copyfile(notes_path, method_path)

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
    lines = [f"# {rec['site']}", ""]
    if card:
        lines += [f"![{rec['urls_saved']} of {rec.get('urls_total') or rec['urls_tried']} pages saved](card.svg)", ""]
    lines += [
        "| | |",
        "|---|---|",
        f"| **Site** | {rec['site']} |",
        f"| **URLs saved** | {rec['urls_saved']} of {rec.get('urls_total') or rec['urls_tried']} |",
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
    lines.append("- [`info.json`](info.json): the same facts in machine-readable form")
    if os.path.isfile(method_path):
        lines.append("- [`method.md`](method.md): how the list of URLs was obtained")
    lines.append("")
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
    parser.add_argument("--notes", default=None, metavar="FILE",
                        help="a text file describing how the list of URLs was obtained; it is "
                             "copied into the record as method.md and linked from its README")
    parser.add_argument("--color", type=_hex_color, default=DEFAULT_ACCENT, metavar="HEX",
                        help=f"accent color of card.svg, like {DEFAULT_ACCENT} (the default)")
    parser.add_argument("--no-card", action="store_true",
                        help="do not write card.svg (and remove it if it is there)")
    parser.add_argument("--out", default=None,
                        help="where to write the record (default: archived-sites/ next to this script)")
    args = parser.parse_args()

    out_root = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "archived-sites")
    os.makedirs(out_root, exist_ok=True)

    rec = build_record(args.site_folder, args.exclude)
    if args.notes and not os.path.isfile(args.notes):
        raise SystemExit(f"Could not find the notes file: {args.notes}")
    folder = write_site_record(rec, out_root, args.notes, card=not args.no_card, accent=args.color)
    write_index(out_root)
    print(f"Recorded {rec['site']}: {rec['urls_saved']} URL(s) saved, "
          f"{rec['urls_failed']} failed.")
    if rec["urls_withheld"]:
        print(f"{rec['urls_withheld']} saved URL(s) were counted but left out of the lists "
              f"(--exclude).")
    print(f"Written to: {folder}")


if __name__ == "__main__":
    sys.exit(main())
