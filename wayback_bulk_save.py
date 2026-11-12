#!/usr/bin/env python3
# Copyright (c) 2026 AccelArch
# SPDX-License-Identifier: MIT
# https://github.com/AccelArch/wayback-bulk-save
"""
Wayback Bulk Save -- saves every page (and downloadable file) of a website to the
Internet Archive's Wayback Machine (web.archive.org), using Save Page Now.

How to use:
1. Run: python3 wayback_bulk_save.py
   The first time, it asks for your Internet Archive keys and saves them to
   config.json (you can also create that file by hand: see config.example.json).
   Get your keys at https://archive.org/account/s3.php
2. After that, every run:
   - The script ASKS for the site URL in the terminal (you never need to edit
     this file to save a new site).
   - It tries to find /sitemap.xml (or sitemap_index.xml, or sitemap-index.xml).
   - If there is no sitemap, it crawls the site starting from the home page,
     following links on the same domain (up to MAX_PAGES pages).
3. The URLs it finds are saved one by one to the Wayback Machine, waiting a
   short interval between requests.
"""

import re
import os
import ssl
import sys
import time
import json
import shutil
import getpass
import threading
import urllib.request
import urllib.parse
from xml.etree import ElementTree
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ============ SSL CERTIFICATE FIX ============
# On some Windows/Termux setups Python cannot find the right root certificates
# and fails with "CERTIFICATE_VERIFY_FAILED". This fixes it by using the
# 'certifi' package, which ships a ready-made list of trusted certificates.
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    print("Warning: the 'certifi' package was not found. If you get an SSL certificate "
          "error, run: pip install certifi")
    SSL_CONTEXT = ssl.create_default_context()
# =============================================

if not HAS_BS4:
    print("Warning: the 'beautifulsoup4' (or 'lxml') package was not found. "
          "Run: pip install beautifulsoup4 lxml -- it is required for crawl mode.")

# ============ TERMINAL COLORS ============
# Enables ANSI color support in the Windows cmd/PowerShell (no install needed)
if sys.platform == "win32":
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)


class C:
    """ANSI color codes to make the terminal output easier to read."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    GRAY = "\033[90m"


def c_ok(text):
    return f"{C.GREEN}{text}{C.RESET}"


def c_err(text):
    return f"{C.RED}{text}{C.RESET}"


def c_warn(text):
    return f"{C.YELLOW}{text}{C.RESET}"


def c_info(text):
    return f"{C.CYAN}{text}{C.RESET}"


def c_title(text):
    return f"{C.BOLD}{C.MAGENTA}{text}{C.RESET}"


def c_dim(text):
    return f"{C.GRAY}{text}{C.RESET}"
# =========================================

# ============ SETTINGS ============
# Your keys are read from config.json (next to this script) -- set them once and
# they work for every site you save afterwards.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _is_unset(value):
    """True if a key is empty or still a placeholder such as YOUR_ACCESS_KEY_HERE."""
    return (not value) or str(value).strip().upper().startswith("YOUR_")


def _load_credentials():
    """Reads the keys from config.json (next to this script). On the first run the
    script asks for them and creates that file (see first_run_setup). Never put keys
    in the code and never commit config.json."""
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"Warning: could not read config.json ({e})")
    return (cfg.get("access_key") or "YOUR_ACCESS_KEY_HERE",
            cfg.get("secret_key") or "YOUR_SECRET_KEY_HERE",
            cfg.get("firecrawl_api_key") or "YOUR_FIRECRAWL_API_KEY_HERE")


ACCESS_KEY, SECRET_KEY, FIRECRAWL_API_KEY = _load_credentials()


def keys_are_set():
    """True when the Internet Archive keys are filled in (no setup needed)."""
    return not (_is_unset(ACCESS_KEY) or _is_unset(SECRET_KEY))


def first_run_setup():
    """Asks for the keys and saves them to config.json, so this only happens once.
    Returns True when the keys are ready to use. It only asks in an interactive
    terminal; anywhere else it returns False without writing anything."""
    global ACCESS_KEY, SECRET_KEY, FIRECRAWL_API_KEY
    if not (sys.stdin and sys.stdin.isatty()):
        return False

    print(c_title("\nFirst-time setup: your Internet Archive keys"))
    print("You only do this once. Your keys are saved in config.json, next to this")
    print("script, and stay on this device.")
    print("Get your free keys at: https://archive.org/account/s3.php\n")

    access = input(c_info("Access key: ")).strip()
    secret = getpass.getpass(c_info("Secret key (hidden while you type or paste): ")).strip()
    if not access or not secret:
        print(c_err("Both keys are required. Nothing was saved."))
        return False
    firecrawl = getpass.getpass(
        c_info("Firecrawl API key (optional, press Enter to skip): ")).strip()

    # Keep anything else already in config.json (for example a Firecrawl key that
    # is already set) instead of overwriting the whole file.
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["access_key"] = access
    cfg["secret_key"] = secret
    if firecrawl or _is_unset(cfg.get("firecrawl_api_key")):
        cfg["firecrawl_api_key"] = firecrawl

    try:
        # 0o600 = only the current user can read the file (Linux, macOS, Termux)
        fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
    except OSError as e:
        print(c_err(f"Could not write config.json: {e}"))
        return False

    ACCESS_KEY, SECRET_KEY = access, secret
    FIRECRAWL_API_KEY = cfg["firecrawl_api_key"] or "YOUR_FIRECRAWL_API_KEY_HERE"
    print(c_ok(f"\nSaved to {CONFIG_PATH}. You will not be asked again.\n"))
    return True
# Internet Archive keys: https://archive.org/account/s3.php
# Firecrawl key (optional): https://www.firecrawl.dev
MAX_PAGES = 50000          # safety limit for crawl mode (used when there is no sitemap)
DELAY_SECONDS = 14        # pause between each save request (avoids rate limiting)

# Save Page Now is asynchronous: when the Archive answers 200 to the POST, that only
# means it ACCEPTED the request into its queue -- not that the capture finished
# successfully. That is why the script works in 3 phases:
#   1) queue ALL pending URLs (without waiting for each one to be confirmed)
#   2) check the status of all queued jobs in bulk, in rounds
#   3) resend (up to SPN_RETRY_ATTEMPTS times) only the ones that failed or were not confirmed
# This avoids getting stuck waiting URL by URL when the Archive's queue is
# congested, while still guaranteeing that "OK" only shows up with real confirmation.
SPN_POLL_INTERVAL_SECONDS = 15    # pause between bulk verification rounds
SPN_POLL_MAX_WAIT_MINUTES = 30    # max time waiting for the WHOLE batch to confirm (not per URL)
SPN_RETRY_ATTEMPTS = 1            # how many times to resend URLs that failed / were not confirmed
SPN_VERIFY_PROGRESS_EVERY = 25    # show a progress line every N status checks
SPN_VERIFY_ERROR_STREAK = 5       # this many failed checks in a row = the Archive is refusing us
SPN_VERIFY_MAX_PAUSES = 6         # pauses (2, 4, 8, 16, 30, 30 min) before giving up for now
SPN_STUCK_JOB_HOURS = 3           # saved jobs older than this that are all still "pending" are stuck
# Sending in a WINDOW: the Archive sometimes accepts requests and then does not finish
# them (it happens when it is busy). So the script never lets more than SPN_MAX_IN_FLIGHT
# accepted-but-unconfirmed jobs pile up, and checks the oldest ones while it keeps sending.
SPN_MAX_IN_FLIGHT = 30            # accepted jobs allowed to wait for confirmation at once
SPN_REAP_EVERY = 25               # after every N URLs sent, check the oldest waiting jobs
SPN_REAP_CHECKS = 40              # how many of the oldest waiting jobs to check each time
SPN_STALL_MINUTES = 45            # waiting jobs but none finished for this long: pause and stop
SPN_STALL_CHECK_MINUTES = 10      # after this long with no job finishing, ask the Archive if it is
                                  # processing anything of yours (0 = it is not: stop right away)

CAPTURE_SCREENSHOT = False   # takes a screenshot of each saved page (like the browser extension does)
CAPTURE_OUTLINKS = False    # False because this script already maps every page via
                             # sitemap/crawl -- no need to ask the Archive for outlinks
                             # again on each one (it would duplicate work). Set to True
                             # if you want it anyway.
CAPTURE_ALL = True           # saves the page even if it returns a 4xx/5xx error

DEDUPE_BY_BASE_URL = True    # ignores variants like ?color=blue, ?size=M -- saves only 1 per page
# List of "cosmetic" query parameters (they do not change the page CONTENT, only
# its look / ordering / tracking) -- only these are ignored by the dedupe.
# ANY other parameter not listed here is treated as possibly being a DIFFERENT
# content page (e.g. ?page=2, ?category=x), so it is not removed -- this avoids
# losing pages by mistake. (A few names are not English -- ordenar, cor, tamanho --
# because they were added after a real case of duplicated pages. If a site uses
# other names for the same thing, the only effect is an extra variant URL being saved.)
DEDUPE_SAFE_PARAMS = {
    "order_key", "orderby", "sort", "sort_by", "ordenar",   # ordering
    "cor", "color", "tamanho", "size",                       # visual product variant
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",  # tracking
    "fbclid", "gclid", "ref",                                 # tracking
}
SKIP_IF_SAVED_WITHIN_DAYS = 0  # do not save again if a snapshot newer than X days already exists
                                # (set 0 to disable this check and always save)

ALLOW_LEVEL1_EXTERNAL_LINKS = True  # False = normal crawl: only follows links that are
                             # INSIDE the path/prefix of the URL you entered
                             # (the usual behavior).
                             # Set True when the site has a pattern like:
                             #   profile page: site.com/profile/username
                             #   file/image inside it: site.com/a/sidhio334 (random
                             #   code, outside the /profile/username prefix)
                             # With True, the crawl also accepts those links "outside
                             # the prefix" BUT ONLY when found inside a page that is
                             # already under the prefix -- it saves that loose link but
                             # does NOT follow the links inside it (it does not expand
                             # one more level), so it never leaks into the whole site.
                             # Set it back to False after using it on a site like that,
                             # if you want the restricted behavior again.

URL_INCLUDE_PATTERN = None  # If set (e.g. r"/property/"), ONLY URLs matching this
                             # pattern (regex) are saved -- useful to focus on real
                             # content pages (e.g. /property/xxx) and ignore
                             # search/filter pages (e.g. /properties/...).
                             # Leave None to disable and keep everything that is
                             # found (default behavior).
CDX_CHECK_PARALLEL_WORKERS = 2  # how many "was this already saved?" checks to run at the SAME
                                   # time (in parallel). Higher = faster, but uses more
                                   # simultaneous connections. 10 is safe.
                                   # If this step produces many errors/timeouts, lower it to 5.

# Path fragments that are NEVER real content -- they are technical endpoints
# (API, login/auth, backend, admin feeds). The crawl ignores any link whose path
# contains one of these fragments, so they never even enter the queue. This stops
# the crawl from generating hundreds of useless variations (e.g. a login language
# selector that creates 1 link per language: ?l=finnish, ?l=polish, ?l=russian...)
# and also avoids getting 429 (rate limit) from the site for hammering an API
# endpoint that has nothing worth archiving.
EXCLUDE_PATH_SNIPPETS = {
    "/api/", "/wp-json/", "/wp-admin/", "/cdn-cgi/", "/auth/",
    "/fg-backend/", "/graphql",
}

# FILE extensions that the crawl must FIND and SAVE to the Wayback Machine (Save
# Page Now saves PDFs normally). The crawl never "opens" these files to look for
# links inside (they are not HTML) -- it only records the URL and queues it.
# Applies to <a href> links found on crawled pages, even when the file lives
# OUTSIDE the prefix (e.g. page at /blog/post but PDF at /wp-content/uploads/).
# Leave empty (set()) to disable.
#
# It also includes software/package DOWNLOAD links: the Wayback Machine saves the
# exact file that was behind the link on that date (e.g. one specific version of
# an .exe), so running again in the future keeps collecting the new versions.
SAVE_FILE_EXTENSIONS = {
    "pdf",
    "doc", "docx", "odt", "rtf",          # documents (Word / LibreOffice)
    "xls", "xlsx", "ods", "csv",          # spreadsheets (Excel / LibreOffice)
    "ppt", "pptx", "odp",                 # presentations (PowerPoint / LibreOffice)
    "epub", "mobi",                       # e-books
    "mp3", "mp4",                         # audio and video (big videos are filtered by MAX_FILE_SIZE_MB)
    "exe", "msi", "msix", "dmg", "pkg", "deb", "rpm", "apk", "appimage",  # installers
    "flatpak", "snap",                                                    # Linux
    "zip", "7z", "rar", "tar", "gz", "tgz", "xz", "bz2",                  # archives
}
# LIGHT files (usually only a few MB): they are sent to the Wayback Machine BEFORE
# the heavy ones (installers, archives), which go last. See "Send order" in the code.
SAVE_LIGHT_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "csv",
    "ppt", "pptx", "odp", "epub", "mobi", "mp3",
}
# SIZE CAP per file (in MB). Before sending each file (PDF, video, installer...)
# the script asks the server for its size WITHOUT downloading it (HEAD request, or a
# 1-byte request) and SKIPS anything above the cap, listing what was skipped in the
# terminal. Skipped files are NOT counted as failures: they are written to
# skipped_too_big_<site>.json, and if you raise the cap later they are included in
# the next run. Applies only to files, never to pages.
# 0 = no limit. If the server does not report the size, the file is sent anyway.
# 200 was chosen because the Wayback Machine already accepted files of ~100 MB in
# tests without problems (and the only documented limit we found is 2 GB), leaving a
# safety margin.
MAX_FILE_SIZE_MB = 200
# Path fragments that mark a download link EVEN WITHOUT a file extension at the end
# (e.g. GitHub Releases, or the "Source code (zip)" of a tag).
SAVE_FILE_PATH_SNIPPETS = {"/releases/download/", "/archive/refs/tags/"}
# True = also saves files hosted on ANOTHER domain, as long as the link is on a
# crawled page (e.g. example.com/download linking to
# github.com/.../releases/download/.../setup.exe). Only the FILE is saved; the crawl
# does NOT leave to explore the other site. False = only files on the same domain.
ALLOW_EXTERNAL_FILE_LINKS = True
# Some sites REPLACE the file at the same link (they upload the new version under the
# same name). With N > 0, files (PDF/.exe/.zip...) saved more than N days ago are
# saved AGAIN on the next run, creating a new snapshot in the Wayback Machine (so
# replaced versions at the same link are also kept). 0 = never save again (old behavior).
# Applies only to files -- normal pages are still skipped if already saved.
RESAVE_FILES_AFTER_DAYS = 7
# ==================================

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    # Language the script asks for when it fetches pages itself (sitemap and crawl).
    # It does not affect what the Archive captures. Change it if you want a site's
    # version in another language.
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
        return resp.read()


# Max time (in minutes) the crawl keeps retrying ONE URL that keeps getting 429
# before giving up on it for good (it then counts in the crawl as a "failed to
# fetch", like any other error).
CRAWL_RATE_LIMIT_MAX_WAIT_MINUTES = 5


def fetch_for_crawl(url, rate_limit_state):
    """
    Like fetch(), but handles rate limiting (HTTP 429) for the parallel crawl
    (several threads hitting the same site at once).

    When a URL gets a 429: that URL waits an increasing time (5s, 10s, 20s...
    up to a 2 min ceiling) and tries again, for up to
    CRAWL_RATE_LIMIT_MAX_WAIT_MINUTES in total -- only then does it give up on it.

    That alone would not be enough: a 429 is usually the WHOLE SITE saying "stop
    hitting me so fast", not something specific to that URL, so the OTHER crawl
    threads (fetching other pages at the same time) are told as well, through
    rate_limit_state, and pause together until the same moment -- that way the crawl
    really "stops" instead of hammering the site with the threads that have not
    received a 429 yet.

    rate_limit_state is a dict shared between the crawl threads:
    {"resume_at": <timestamp>, "lock": <threading.Lock>}.
    """
    wait_seconds = 5
    total_waited = 0

    while True:
        # If another thread already signaled a pause in progress (it got a 429
        # before me), wait for it to pass before trying -- this prevents all the
        # threads from getting a 429 at the same time purely from lack of sync.
        with rate_limit_state["lock"]:
            resume_at = rate_limit_state["resume_at"]
        remaining = resume_at - time.time()
        if remaining > 0:
            time.sleep(remaining)

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise

            if total_waited >= CRAWL_RATE_LIMIT_MAX_WAIT_MINUTES * 60:
                # Give up on this URL -- process_page handles it as a normal failure
                raise

            print(c_warn(f"  .. [Original site] rate limit (429) on "
                  f"{url[:70]}{'...' if len(url) > 70 else ''} -- pausing the "
                  f"crawl for {wait_seconds}s before trying again (backing off)."))

            with rate_limit_state["lock"]:
                new_resume_at = time.time() + wait_seconds
                if new_resume_at > rate_limit_state["resume_at"]:
                    rate_limit_state["resume_at"] = new_resume_at

            time.sleep(wait_seconds)
            total_waited += wait_seconds
            wait_seconds = min(wait_seconds * 2, 120)  # backing off, up to 2 min per wait


def try_sitemaps(base):
    """Tries to find URLs through sitemap.xml (including a sitemap index with sub-sitemaps)."""
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
    ]
    urls = set()
    for sm_url in candidates:
        try:
            data = fetch(sm_url)
        except Exception:
            continue

        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            continue

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # In case it is a sitemap index (it points to other sitemaps)
        sub_sitemaps = [el.text for el in root.findall(".//sm:sitemap/sm:loc", ns)]
        if sub_sitemaps:
            print(c_info(f"Sitemap index found with {len(sub_sitemaps)} sub-sitemaps."))
            for sub in sub_sitemaps:
                try:
                    sub_data = fetch(sub)
                    sub_root = ElementTree.fromstring(sub_data)
                    for loc in sub_root.findall(".//sm:url/sm:loc", ns):
                        urls.add(loc.text.strip())
                except Exception as e:
                    print(c_err(f"  Failed to read sub-sitemap {sub}: {e}"))
        else:
            # Regular sitemap (a direct list of URLs)
            for loc in root.findall(".//sm:url/sm:loc", ns):
                urls.add(loc.text.strip())

        if urls:
            print(c_ok(f"Sitemap found at {sm_url} with {len(urls)} URLs."))
            return urls

    return urls


def parsed_ok(parsed):
    """http(s) URL whose path is a file to save (see is_saveable_file)."""
    return parsed.scheme in ("http", "https") and is_saveable_file(parsed)


def is_saveable_file(parsed):
    """True if the URL (already passed through urlparse) is a file/download to save:
    its extension is in SAVE_FILE_EXTENSIONS or its path is in SAVE_FILE_PATH_SNIPPETS."""
    if not SAVE_FILE_EXTENSIONS:  # set() = feature disabled
        return False
    path = parsed.path.lower()
    if any(s in path for s in SAVE_FILE_PATH_SNIPPETS):
        return True
    name = path.rsplit("/", 1)[-1]
    return "." in name and name.rsplit(".", 1)[-1] in SAVE_FILE_EXTENSIONS


def crawl_site(base, max_pages, state_file=None):
    """
    Simple crawl: follows internal links starting from the home page. Shows live progress.

    If state_file is given and already exists from a previous run (for example,
    because the MAX_PAGES limit was hit last time), the crawl RESUMES from the queue
    and the already-visited pages saved there, instead of starting from the home page
    again. The state is saved periodically (every 50 pages) and also at the end / when
    the limit is hit, so you can continue from where it stopped in any situation.
    """
    parsed_base = urllib.parse.urlparse(base)
    domain = parsed_base.netloc
    # Restricts the crawl to only following links WHOSE PATH STARTS WITH the path of
    # the base URL you gave. E.g. base = https://site.com/user/research-topic
    # -> it only enters /user/research-topic/anything, never /user/other-topic or the
    # site's home page. This keeps the crawl from "climbing up" and sweeping the whole
    # site/forum (a giant/infinite loop).
    path_prefix = parsed_base.path if parsed_base.path not in ("", "/") else None
    if path_prefix:
        print(c_info(f"Crawl restricted to the path: {path_prefix}* "
              f"(it will not leave this folder/section of the site)."))

    visited = set()
    queue = deque([base])
    # URLs that were accepted as "1 hop outside the prefix" (e.g. an image with a
    # random code linked from inside a page under the prefix). They are
    # visited/saved normally, but the links THEY CONTAIN are not followed -- this
    # keeps the crawl from leaking into the whole site through them.
    external_leaf = set()
    # Files (PDF etc. -- see SAVE_FILE_EXTENSIONS) found on pages: they are not
    # visited/opened, they only go into the final result to be saved to the Wayback Machine.
    files_found = set()

    resumed = False
    if state_file:
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            visited = set(state.get("visited", []))
            queue = deque(state.get("queue", [base]))
            external_leaf = set(state.get("external_leaf", []))
            files_found = set(state.get("files_found", []))
            # Removes from the resumed queue any URL that matches the current
            # exclusion patterns -- useful if you adjusted EXCLUDE_PATH_SNIPPETS after
            # those "wrong" URLs had already entered the queue in a previous run
            # (e.g. API/backend endpoints causing 429).
            before = len(queue)
            queue = deque(
                u for u in queue
                if not any(s in urllib.parse.urlparse(u).path.lower() for s in EXCLUDE_PATH_SNIPPETS)
            )
            removed = before - len(queue)
            resumed = True
            print(c_info(f"Previous crawl state found: resuming with "
                  f"{len(visited)} pages already visited and {len(queue)} in the queue."))
            if removed:
                print(c_ok(f"  -> {removed} technical endpoint URL(s) (API/backend) "
                      f"removed from the resumed queue."))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(c_warn(f"Warning: could not read the previous crawl state ({e}). "
                  f"Starting from scratch."))

    if not resumed:
        print("No sitemap found. Crawling manually from the home page...")
        print("(this can take a while depending on the size of the site -- the progress below "
              "is updated after every page visited)\n")

    # Common sorting/display parameters that generate MANY variations of the SAME
    # page (e.g. ?order_key=name_asc, ?order_key=price_desc) -- we ignore them here in
    # the crawl so we do not waste MAX_PAGES slots on variations that the dedupe
    # would remove anyway later.
    ORDER_PARAM_NAMES = {"order_key", "orderby", "sort", "sort_by", "ordenar"}

    def strip_sort_params(url):
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return url
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        kept = [(k, v) for k, v in params if k.lower() not in ORDER_PARAM_NAMES]
        new_query = urllib.parse.urlencode(kept)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    def save_state():
        if state_file:
            try:
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump({"visited": list(visited), "queue": list(queue),
                               "external_leaf": list(external_leaf),
                               "files_found": list(files_found)}, f)
            except Exception:
                pass

    # Rate-limit state shared between the crawl threads (see fetch_for_crawl): when
    # a URL gets a 429, this timestamp is updated and all the other threads pause
    # together until it passes.
    rate_limit_state = {"resume_at": 0.0, "lock": threading.Lock()}

    def process_page(url):
        """Fetches one page and extracts its links. Runs in parallel (thread)."""
        try:
            html = fetch_for_crawl(url, rate_limit_state).decode("utf-8", errors="ignore")
        except Exception as e:
            return url, None, str(e), []

        # Page marked as an "external leaf" (1 hop outside the prefix): its content
        # is saved and NO page links are followed from here on -- but it still
        # collects its FILE/download links (PDF, installers...), otherwise pages like
        # /apps/some-app (full of downloads) would end up without them.
        is_leaf = url in external_leaf

        found_links = []
        external_found = []
        soup = BeautifulSoup(html, "lxml")

        # Embedded video/audio (<video>, <audio>, <source>): not an <a> link, but it is
        # a file we want to save (e.g. an .mp4/.mp3 hosted on the site itself).
        for media in soup.find_all(["video", "audio", "source"]):
            for attr in ("src", "data-src"):
                if media.get(attr):
                    mfull = urllib.parse.urljoin(url, media[attr]).split("#")[0]
                    mparsed = urllib.parse.urlparse(mfull)
                    if parsed_ok(mparsed) and (mparsed.netloc == domain or ALLOW_EXTERNAL_FILE_LINKS):
                        files_found.add(mfull)

        for a in soup.find_all("a", href=True):
            full = urllib.parse.urljoin(url, a["href"])
            full = full.split("#")[0]  # remove anchors
            full = strip_sort_params(full)
            parsed = urllib.parse.urlparse(full)
            # file/download we want to save (PDF, .exe, .zip...): record it and move
            # on, without putting it in the queue (it is not HTML) and without requiring
            # the crawl prefix. It may be on another domain if ALLOW_EXTERNAL_FILE_LINKS.
            if parsed.scheme in ("http", "https") and is_saveable_file(parsed) \
                    and (parsed.netloc == domain or ALLOW_EXTERNAL_FILE_LINKS):
                files_found.add(full)  # set.add is thread-safe (GIL)
                continue
            if is_leaf:
                continue  # leaf: does not follow page links
            if parsed.netloc == domain:
                # ignore technical endpoints (API/auth/backend) -- never real
                # content, they only waste queue slots and can cause 429
                if any(snippet in parsed.path.lower() for snippet in EXCLUDE_PATH_SNIPPETS):
                    continue
                if path_prefix and not parsed.path.startswith(path_prefix):
                    # outside the prefix -- only accepted if ALLOW_LEVEL1_EXTERNAL_LINKS
                    # is on, and even then it is marked as a leaf (it does not expand)
                    if ALLOW_LEVEL1_EXTERNAL_LINKS:
                        external_found.append(full)
                    continue
                if not re.search(r"\.(jpg|jpeg|png|gif|svg|css|js|pdf|zip)$", parsed.path, re.IGNORECASE):
                    found_links.append(full)
        return url, found_links, None, external_found

    CRAWL_PARALLEL_WORKERS = 8  # how many pages to fetch at the SAME TIME during the crawl

    while queue and len(visited) < max_pages:
        # Takes a batch of URLs from the queue to process in parallel (only the ones
        # not yet visited, respecting the max_pages limit)
        batch = []
        while queue and len(batch) < CRAWL_PARALLEL_WORKERS and len(visited) + len(batch) < max_pages:
            url = strip_sort_params(queue.popleft())
            if url not in visited and url not in batch:
                batch.append(url)

        if not batch:
            break

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [executor.submit(process_page, u) for u in batch]
            for future in as_completed(futures):
                url, found_links, error, external_found = future.result()
                visited.add(url)

                short_url = url if len(url) <= 70 else url[:67] + "..."
                if error:
                    print(c_dim(f"  [{len(visited)} visited | {len(queue)} in queue]")
                          + f" Analyzing: {short_url}")
                    print(c_err(f"    -> [Original site] Failed to fetch this page: {error}"))
                    continue

                new_links = 0
                for full in found_links:
                    if full not in visited and full not in queue:
                        queue.append(full)
                        new_links += 1
                for full in external_found:
                    # link outside the prefix, accepted as a "1 hop" -- goes into the
                    # queue to be saved, but marked as a leaf (it does not expand further)
                    if full not in visited and full not in queue:
                        queue.append(full)
                        external_leaf.add(full)
                        new_links += 1

                print(c_dim(f"  [{len(visited)} visited | {len(queue)} in queue]")
                      + f" Analyzing: {short_url}")
                if new_links:
                    print(c_ok(f"    -> +{new_links} new links found on this page"))

        if len(visited) % 50 < len(batch):
            save_state()

        time.sleep(0.3)  # short pause between batches, to stay polite to the site

    if len(visited) >= max_pages:
        print(c_warn(f"\nReached the safety limit of {max_pages} pages (MAX_PAGES). "
              f"There may be more pages not visited -- raise MAX_PAGES and run again "
              f"to CONTINUE from where it stopped (the progress was saved)."))
        save_state()
    elif state_file:
        # The crawl really finished (the queue is empty) -- clears the saved state,
        # since there is nothing left to resume
        try:
            import os
            if os.path.exists(state_file):
                os.remove(state_file)
        except Exception:
            pass

    if files_found:
        print(c_ok(f"Files found on the pages (e.g. PDF): {len(files_found)} "
                   f"-- they will be saved together with the pages."))
    print(c_title(f"\nCrawl finished: {len(visited)} pages found."))
    return visited | files_found


def dedupe_by_base_url(urls):
    """
    Groups URLs that differ only by parameters KNOWN TO BE COSMETIC
    (DEDUPE_SAFE_PARAMS: ordering, color/size variant, tracking) and
    keeps only ONE per group.

    ANY other parameter (e.g. ?page=2, ?category=x, anything not listed in
    DEDUPE_SAFE_PARAMS) IS KEPT in the comparison -- that is, URLs that differ
    only by an unknown parameter are treated as DIFFERENT pages and both are
    kept. This avoids losing real content (like pagination) just because the
    dedupe thought it was "the same page".

    Keeps the shortest URL of each group (usually the "clean" one, without
    cosmetic parameters).
    """
    groups = {}
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        # Keeps only the parameters that are NOT in the "safe to ignore" list
        kept_params = [(k, v) for k, v in params if k.lower() not in DEDUPE_SAFE_PARAMS]
        kept_params.sort()  # order should not matter for grouping purposes
        query_key = urllib.parse.urlencode(kept_params)
        base = parsed.scheme + "://" + parsed.netloc + parsed.path.rstrip("/") + "?" + query_key
        if base not in groups or len(url) < len(groups[base]):
            groups[base] = url
    return set(groups.values())


def get_remote_size(url, timeout=25):
    """File size in bytes WITHOUT downloading it, or None if the server does not report it.
    Tries HEAD (follows redirects, e.g. GitHub Releases); if the server refuses
    HEAD, it asks for only the 1st byte (Range) and reads the total from Content-Range."""
    try:
        req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit():
                return int(length)
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            content_range = resp.headers.get("Content-Range", "")  # e.g. "bytes 0-0/12345"
            if "/" in content_range and content_range.rsplit("/", 1)[-1].isdigit():
                return int(content_range.rsplit("/", 1)[-1])
            length = resp.headers.get("Content-Length")  # the server ignored the Range
            if length and length.isdigit() and resp.status == 200:
                return int(length)
    except Exception:
        pass
    return None


def skip_oversized(url_list, max_mb, cache_file, skipped_file, max_workers=4):
    """Removes from the list the FILES (not the pages) larger than max_mb. The sizes
    that were looked up are kept in cache_file (they are not asked again on the next run)."""
    cache = {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass

    files = [u for u in url_list if is_saveable_file(urllib.parse.urlparse(u))]
    to_check = [u for u in files if u not in cache]
    if to_check:
        print(c_info(f"\nChecking the size of {len(to_check)} file(s) (cap: {max_mb} MB) "
              f"-- this only asks the server, nothing is downloaded..."))
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for u, size in zip(to_check, executor.map(get_remote_size, to_check)):
                cache[u] = size
                done += 1
                if done % 50 == 0:
                    print(c_dim(f"  ... {done}/{len(to_check)} checked"))
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    limit = max_mb * 1024 * 1024
    too_big = {u: cache[u] for u in files if cache.get(u) and cache[u] > limit}
    unknown = [u for u in files if cache.get(u) is None]
    if too_big:
        print(c_warn(f"Skipping {len(too_big)} file(s) above {max_mb} MB "
              f"(they do not count as failures; raise MAX_FILE_SIZE_MB to include them):"))
        for u, size in sorted(too_big.items(), key=lambda kv: -kv[1])[:15]:
            print(c_warn(f"   {size / 1048576:8.1f} MB  {u if len(u) <= 90 else u[:87] + '...'}"))
        if len(too_big) > 15:
            print(c_warn(f"   ... and {len(too_big) - 15} more (full list in {os.path.basename(skipped_file)})"))
        try:
            with open(skipped_file, "w", encoding="utf-8") as f:
                json.dump(too_big, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    if unknown:
        print(c_dim(f"{len(unknown)} file(s) with no size reported by the server: they will be sent anyway."))
    return [u for u in url_list if u not in too_big]


def already_saved_recently(url, days=7, max_retries=3):
    """
    Asks the Wayback Machine's CDX API whether this URL already has a SUCCESSFUL
    snapshot (2xx status) saved within the last N days. If it does, there is no
    need to save it again.

    Important: it looks at the status code of each snapshot, not just whether a
    timestamp exists -- a snapshot of an error page (404/500) or of a redirect does
    not count as "already saved successfully", otherwise the script would wrongly
    skip or confirm as OK a URL whose last real capture was bad/incomplete.

    If the query fails (timeout, momentary network error), it tries again up to
    max_retries times before giving up -- this avoids saving again a URL that WAS
    already saved just because the check failed by chance.

    Returns True/False. If every attempt fails, it returns None (meaning
    "could not confirm" -- the caller decides what to do in that case).
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            cdx_url = (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={urllib.parse.quote(url, safe='')}"
                "&output=json&limit=-1&fl=timestamp,statuscode"
            )
            data = fetch(cdx_url)
            rows = json.loads(data)
            if len(rows) < 2:  # the first row is the header
                return False
            # Keeps only the snapshots with a 2xx status -- ignores error pages
            # (404/500) and redirects (3xx) that do not represent the real content.
            good_rows = [
                r for r in rows[1:]
                if str(r[1] if len(r) > 1 else "").strip().startswith("2")
            ]
            if not good_rows:
                return False
            last_timestamp = good_rows[-1][0]  # format: YYYYMMDDhhmmss
            last_date = time.strptime(last_timestamp[:8], "%Y%m%d")
            last_epoch = time.mktime(last_date)
            age_days = (time.time() - last_epoch) / 86400
            return age_days < days
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(1.5 * attempt)  # waits a bit longer on each attempt
            continue
    # Every attempt failed -- we could not confirm
    return None


def already_saved_recently_batch(urls, days=7, max_workers=10):
    """
    Applies already_saved_recently to a list, IN PARALLEL using several threads
    (the CDX check is read-only, so many can run at the same time without the
    rate-limit risk that Save Page Now has). max_workers controls how many
    simultaneous queries -- 10 is a safe value that already speeds things up a lot
    without overloading the service.
    """
    to_save = []
    skipped = 0
    unconfirmed = 0
    total = len(urls)
    done = 0

    def check(url):
        return url, already_saved_recently(url, days=days)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check, url): url for url in urls}
        for future in as_completed(futures):
            done += 1
            pct = int((done / total) * 100)
            print(c_dim(f"  [{done}/{total} - {pct}%] Checking in parallel..."),
                  end="\r", flush=True)
            try:
                url, is_recent = future.result()
                if is_recent is True:
                    skipped += 1
                elif is_recent is False:
                    to_save.append(url)
                else:
                    # None = every check attempt failed even with retries.
                    # To be safe, it is saved anyway (better to save again than to
                    # risk losing a URL that was never really saved).
                    unconfirmed += 1
                    to_save.append(url)
            except Exception:
                # Unexpected error in the thread itself -- assume it needs saving
                unconfirmed += 1
                to_save.append(futures[future])

    print(" " * 60, end="\r")  # clears the progress line
    print(c_info(f"Check finished: {skipped} already had a recent snapshot (skipped), "
          f"{len(to_save)} will be saved now."))
    if unconfirmed:
        print(c_warn(f"  (of those, {unconfirmed} could not be confirmed even after "
              f"retries on the [CDX API] -- they will be saved to be safe)"))
    return to_save


_KNOWN_STATUS_FIELDS = {"status", "job_id", "resources", "timestamp", "original_url", "duration_sec",
                        "counters", "http_status", "mimetype", "content_length", "message",
                        "status_ext", "screenshot", "first_archive", "outlinks", "url"}
_SEEN_STATUS_FIELDS = set()
_SEEN_ARCHIVE_MESSAGES = set()


def _note_unknown_fields(data):
    """The status answer sometimes carries fields this script does not use. Show each new
    one once, so we can learn what the Archive tells us (for example, a busy message)."""
    for key in set(data) - _KNOWN_STATUS_FIELDS - _SEEN_STATUS_FIELDS:
        _SEEN_STATUS_FIELDS.add(key)
        print(c_dim(f"     (the Archive's status answer has a field this script does not use yet: "
                    f"'{key}' = {str(data[key])[:80]})"))


def _note_status_message(info):
    """Shows, once per distinct text, what the Archive says about a job that is still pending."""
    if ": " in info and not info.startswith("error"):
        text = info.split(": ", 1)[1]
        if text not in _SEEN_ARCHIVE_MESSAGES:
            _SEEN_ARCHIVE_MESSAGES.add(text)
            print(c_info(f"     Archive says: {text}"))


class ArchiveStalled(Exception):
    """Jobs are waiting at the Archive, but none has finished for too long."""


def get_user_status(access_key, secret_key):
    """Asks the Archive how busy YOUR account is (read-only). Returns a dict such as
    {"processing": 0, "available": 3, "daily_captures": 91, "daily_captures_limit": 30000,
     "daily_status": 838, "daily_status_limit": 70000}, or None if it could not be read.
    "processing" = captures of yours running right now; "available" = free slots."""
    req = urllib.request.Request(
        "https://web.archive.org/save/status/user",
        headers={**HEADERS, "Accept": "application/json",
                 "Authorization": f"LOW {access_key}:{secret_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read().decode(errors="ignore"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def describe_user_status(st):
    """One readable line about the account's limits."""
    parts = []
    if st.get("available") is not None and st.get("processing") is not None:
        total = st["available"] + st["processing"]
        parts.append(f"{st['processing']} of {total} capture slots busy")
    if st.get("daily_captures") is not None:
        parts.append(f"captures today {st['daily_captures']}/{st.get('daily_captures_limit', '?')}")
    if st.get("daily_status") is not None:
        parts.append(f"status checks today {st['daily_status']}/{st.get('daily_status_limit', '?')}")
    return " | ".join(parts)


def check_job_status_once(job_id, access_key, secret_key):
    """
    A single check (no loop/waiting) of https://web.archive.org/save/status/{job_id}.

    Returns (resolved: bool, ok: bool, info: str):
    - resolved=True, ok=True   -> job confirmed successful
    - resolved=True, ok=False  -> job confirmed as an error
    - resolved=False           -> still pending, or a network error while checking (info says which)
    """
    status_url = f"https://web.archive.org/save/status/{job_id}"
    req = urllib.request.Request(
        status_url,
        headers={
            **HEADERS,
            "Accept": "application/json",
            "Authorization": f"LOW {access_key}:{secret_key}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode(errors="ignore")
        data = json.loads(raw)
        job_status = data.get("status")
        _note_unknown_fields(data)

        if job_status == "success":
            return True, True, "success"
        if job_status == "error":
            err_msg = data.get("message") or data.get("status_ext") or raw[:200]
            return True, False, f"error: {err_msg}"
        # Still pending. If the Archive says something about it (for example that it is
        # busy), keep that text so it can be shown.
        note = data.get("message") or data.get("status_ext")
        return False, False, (f"{job_status or 'pending'}: {note}" if note else (job_status or "pending"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # The Archive does not know this job (it expired, or was never created).
            # Waiting longer will not help, so report it as a final failure: the retry
            # step then checks the index and sends the URL again if it is really missing.
            return True, False, "error: job not found at the Archive (expired or never created)"
        if e.code in (401, 403):
            return False, False, f"error checking status (HTTP {e.code}: the Archive rejected your keys)"
        if e.code == 429:
            return False, False, "error checking status (HTTP 429: too many status checks, slow down)"
        return False, False, f"error checking status (HTTP {e.code})"
    except Exception as e:
        return False, False, f"error checking status ({e})"


class VerificationBlocked(Exception):
    """The Archive kept refusing the status checks, even after several pauses."""


def verify_all_jobs(submitted, access_key, secret_key,
                     poll_interval=SPN_POLL_INTERVAL_SECONDS,
                     max_wait_minutes=SPN_POLL_MAX_WAIT_MINUTES,
                     status_stagger_seconds=1, on_resolved=None,
                     progress_every=SPN_VERIFY_PROGRESS_EVERY,
                     error_streak_limit=SPN_VERIFY_ERROR_STREAK,
                     max_block_pauses=SPN_VERIFY_MAX_PAUSES):
    """
    Verifies, IN BULK, all the jobs queued in the submission phase, in rounds:
    in each round it checks the status of ALL jobs still pending (one after the
    other, with a short pause between checks so the status endpoint is not
    hammered), removes the ones that already resolved from the list, waits
    poll_interval, and repeats -- until all of them resolve or max_wait_minutes
    runs out in total (not per individual job).

    `submitted` is a list of dicts with at least {"url":..., "job_id":...}.

    `on_resolved(item, ok, info)`, if given, is called THE MOMENT each job resolves
    (success or an error reported by the API itself) -- it is used to persist the
    result to disk immediately, instead of only at the end of a round that, with
    many jobs, can take hours. Jobs that hit the overall timeout (still pending when
    the time runs out) do NOT go through it -- those keep being handled by the
    caller of this function, by looking at the returned dict.

    Returns a dict {job_id: (ok: bool, info: str)} with the final result of each
    job (the ones that did not resolve in time are returned as (False, "timeout...")).
    """
    pending = list(submitted)
    resolved = {}
    started = time.time()
    passes = 0
    paused_total = 0   # seconds spent waiting because the Archive was refusing connections
    error_streak = 0   # failed checks in a row (reset by any real answer)
    block_pauses = 0   # pauses taken in a row, with no real answer in between

    # One pass checks every pending job once (about 1-2 s per job), so a big batch takes
    # a long time. Say so up front, otherwise a silent first pass looks like a freeze.
    if len(pending) > 50:
        est_min = max(1, int(len(pending) * (status_stagger_seconds + 1) / 60))
        print(c_info(f"     Each pass checks every job once and takes roughly {est_min} min "
                     f"for {len(pending)} jobs. Progress is shown every {progress_every} checks."))

    while pending:
        passes += 1
        total = len(pending)
        still_pending = []
        confirmed = failed = 0
        check_errors = {}  # reason -> how many checks failed for that reason
        stopped_early = False

        for idx, item in enumerate(pending, 1):
            is_resolved, ok, info = check_job_status_once(item["job_id"], access_key, secret_key)
            if (not is_resolved) and info.startswith("error checking status"):
                error_streak += 1
            else:
                error_streak = 0
                block_pauses = 0
            if is_resolved:
                resolved[item["job_id"]] = (ok, info)
                confirmed += 1 if ok else 0
                failed += 0 if ok else 1
                tag = c_ok("OK ✓") if ok else c_err(f"FAILED ✗ ({info})")
                print(f"  [{idx}/{total}] {item['url']} -> {tag}")
                if on_resolved:
                    on_resolved(item, ok, info)
            else:
                still_pending.append(item)
                if info.startswith("error checking status"):
                    # The check itself failed (rate limit, rejected keys, network...).
                    # Show each distinct reason the first time it happens.
                    if info not in check_errors:
                        print(c_warn(f"  !! {info}"))
                    check_errors[info] = check_errors.get(info, 0) + 1
                else:
                    _note_status_message(info)

            # Several failed checks in a row: the Archive is refusing or limiting this
            # connection. Hammering it (1 check per second) only makes that worse, so
            # pause, with growing waits, and start a new pass afterwards. The jobs
            # stay pending; nothing is marked as failed because of this.
            if error_streak >= error_streak_limit:
                block_pauses += 1
                if block_pauses > max_block_pauses:
                    raise VerificationBlocked(info)
                wait = min(120 * 2 ** (block_pauses - 1), 1800)
                print(c_warn(f"\n  !! {error_streak} status checks failed in a row (last: {info})."))
                print(c_warn(f"     The Archive may be limiting this connection. Pausing {wait // 60} min "
                             f"before trying again ({block_pauses}/{max_block_pauses}). "
                             f"Stopping the script now is safe: unconfirmed jobs are kept."))
                time.sleep(wait)
                paused_total += wait
                error_streak = 0
                still_pending.extend(pending[idx:])  # not checked yet -> next pass
                stopped_early = True
                break

            if progress_every and idx % progress_every == 0 and idx != total:
                print(c_dim(f"     .. checked {idx}/{total}: {confirmed} confirmed, {failed} failed, "
                            f"{len(still_pending)} not confirmed yet"))
            if status_stagger_seconds:
                time.sleep(status_stagger_seconds)

        if stopped_early:
            pending = still_pending
            print(c_dim(f"  Pass {passes} stopped early ({confirmed} confirmed so far). "
                        f"Starting a new pass with the {len(pending)} job(s) left."))
            continue

        pending = still_pending
        print(c_dim(f"  Pass {passes} done: {total} checked -> {confirmed} confirmed, "
                    f"{failed} failed, {len(pending)} not confirmed yet."))
        if check_errors:
            n_err = sum(check_errors.values())
            print(c_warn(f"  {n_err} of the {total} checks could not be completed:"))
            for reason, count in sorted(check_errors.items(), key=lambda kv: -kv[1]):
                print(c_warn(f"    {count} x {reason}"))
            if n_err == total:
                print(c_err("  Every status check failed, so nothing could be verified. Check your "
                            "keys and your connection, then run the script again: the pending "
                            "jobs are kept and will be checked again."))
        elif pending:
            print(c_dim("  The Archive has accepted these jobs but has not finished them yet "
                        "(it processes the queue slowly when it is busy)."))
        if not pending:
            break

        # Real elapsed time, not the sum of the waits. Always do at least 2 full passes,
        # so a huge batch is not cut off in the middle of the first one.
        elapsed = time.time() - started - paused_total  # waiting out a block does not count
        if elapsed >= max_wait_minutes * 60 and passes >= 2:
            print(c_warn(f"\n  !! Overall verification timeout ({max_wait_minutes} min): "
                  f"{len(pending)} job(s) are still not confirmed and will be marked as failed "
                  f"(they can be resent in the retry step)."))
            for item in pending:
                resolved[item["job_id"]] = (False, "timed out waiting for confirmation")
            break

        print(c_dim(f"     .. {len(pending)} job(s) not confirmed yet, "
              f"waiting {poll_interval}s before the next pass "
              f"(elapsed: {int(elapsed // 60)} min / {max_wait_minutes} min)..."))
        time.sleep(poll_interval)

    return resolved


def jobs_look_stuck(jobs, access_key, secret_key, sample_size=8, delay_seconds=2):
    """Checks a small sample of OLD jobs, slowly. The Archive normally finishes a
    capture in minutes, so jobs that are still "pending" hours later are stuck and
    will probably never finish.

    Returns True  if every sampled job is still pending (they look stuck),
            False if at least one finished or failed (normal verification will work),
            None  if the sample could not be checked (connection errors)."""
    if not jobs:
        return False
    step = max(1, len(jobs) // sample_size)
    sample = jobs[::step][:sample_size]
    still_pending = finished = errors = 0
    for job in sample:
        is_resolved, ok, info = check_job_status_once(job["job_id"], access_key, secret_key)
        if is_resolved:
            finished += 1
        elif info.startswith("error checking status"):
            errors += 1
        else:
            still_pending += 1
        time.sleep(delay_seconds)
    print(c_dim(f"  Sample of {len(sample)} old job(s): {still_pending} still pending, "
                f"{finished} finished, {errors} could not be checked."))
    if finished:
        return False
    if errors > len(sample) // 2 or still_pending == 0:
        return None
    return True


def enqueue_to_wayback(url, access_key, secret_key, max_wait_minutes=30):
    """
    Only the QUEUEING step: sends the POST to Save Page Now and returns as soon as
    the Internet Archive accepts the request into its queue (it does not wait for
    confirmation that the capture succeeded -- that is done later, in bulk, by
    verify_all_jobs).

    On a rate limit (HTTP 429) or a temporary server error (5xx), the script
    PAUSES by itself, waits an increasing time (1 min, 2 min, 4 min...) and tries
    again, up to a total time limit (max_wait_minutes). If that limit is exceeded,
    it gives up for now and the URL is retried in the final round.

    Returns (accepted: bool, status: int|None, job_id_or_detail: str).
    If accepted=True, job_id_or_detail is the job_id. If accepted=False, it is the reason.
    """
    api_url = "https://web.archive.org/save/"

    payload = {"url": url}
    if CAPTURE_SCREENSHOT:
        payload["capture_screenshot"] = "1"
    if CAPTURE_OUTLINKS:
        payload["capture_outlinks"] = "1"
    if CAPTURE_ALL:
        payload["capture_all"] = "1"

    wait_seconds = 60
    total_waited = 0

    while True:
        req = urllib.request.Request(
            api_url,
            data=urllib.parse.urlencode(payload).encode(),
            headers={
                **HEADERS,
                "Accept": "application/json",
                "Authorization": f"LOW {access_key}:{secret_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
                status = resp.status
                body = resp.read().decode(errors="ignore")

            # POST accepted (status < 400) -- this is NOT confirmation of success,
            # it only means the job entered the queue.
            job_id = None
            parsed_body = None
            try:
                parsed_body = json.loads(body)
                job_id = parsed_body.get("job_id")
            except Exception:
                pass

            if not job_id:
                # Even with HTTP 200, the body can carry an application-level error
                # (e.g. {"status":"error","status_ext":"error:host-crawling-paused"}).
                # Some of these are temporary -- Save Page Now pauses captures of a
                # specific host for a while so it does not overload the target site --
                # and deserve the same retry with backoff that we already give to
                # 429/5xx, instead of giving up on the URL right away.
                status_ext = (parsed_body or {}).get("status_ext", "") or ""
                TEMPORARY_STATUS_EXT_SUBSTRINGS = (
                    "host-crawling-paused",
                    "user-session-limit",
                    "internal-error",
                    "proxy-error",
                    "read-timeout",
                    "connection-timeout",
                    "no-browsers-available",
                )
                if any(marker in status_ext for marker in TEMPORARY_STATUS_EXT_SUBSTRINGS):
                    app_msg = (parsed_body or {}).get("message", status_ext)

                    if total_waited >= max_wait_minutes * 60:
                        print(c_warn(f"\n  !! [Save Page Now] Still in a temporary pause ({status_ext}) "
                              f"after waiting {max_wait_minutes} min -- skipping for now, "
                              f"this URL goes into the final retry round."))
                        return False, status, f"TIMEOUT_TEMP::temporary pause ({status_ext}): {app_msg}"

                    print(c_warn(f"  .. [Save Page Now] {status_ext} ({app_msg}). "
                          f"Waiting {wait_seconds}s before trying again..."))
                    time.sleep(wait_seconds)
                    total_waited += wait_seconds
                    wait_seconds = min(wait_seconds * 2, 600)
                    continue

                snippet = body[:120].replace("\n", " ")
                looks_like_html = body.lstrip().lower().startswith(("<!doctype", "<html"))
                reason = "the response came back as HTML, not JSON (the API did not recognize the request)" if looks_like_html else f"unexpected response body: {snippet}"
                return False, status, reason

            return True, status, job_id
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read().decode(errors="ignore")
            except Exception:
                # reading the error body can itself time out / reset the
                # connection -- if that happens, do not let it crash the
                # script, just continue without the body (it is not essential
                # for the retry to work)
                body = ""

            # Rate limit or temporary server overload -> wait and try again
            if status == 429 or status >= 500:
                body_snippet = body.strip().replace("\n", " ")[:200]
                if body_snippet:
                    print(c_dim(f"     (server response: {body_snippet})"))

                if total_waited >= max_wait_minutes * 60:
                    print(c_warn(f"\n  !! [Save Page Now] Still rate limited / erroring (status={status}) after "
                          f"waiting {max_wait_minutes} min -- skipping for now, "
                          f"this URL goes into the final retry round."))
                    return False, status, f"TIMEOUT_TEMP::rate limit/error (status={status}): {body[:200]}"

                print(c_warn(f"  .. [Save Page Now] status={status} (rate limit / temporary error). "
                      f"Waiting {wait_seconds}s before trying again..."))
                time.sleep(wait_seconds)
                total_waited += wait_seconds
                wait_seconds = min(wait_seconds * 2, 600)  # backoff, up to 10 min per wait
                continue

            # Another kind of error (a 4xx that is not a rate limit) -> report it and give up on this URL
            return False, status, body[:200]
        except Exception as e:
            # Timeout, connection reset, or any other network error that is not a
            # regular HTTPError. This used to give up right away -- now we treat it
            # the same as a temporary rate limit: wait and try again, with the same
            # total time limit.
            if total_waited >= max_wait_minutes * 60:
                print(c_warn(f"\n  !! [Save Page Now] Still getting a connection error ({e}) after "
                      f"waiting {max_wait_minutes} min -- skipping for now, "
                      f"this URL goes into the final retry round."))
                return False, None, f"TIMEOUT_TEMP::connection error: {e}"

            print(c_warn(f"  .. [Save Page Now] connection error ({e}). "
                  f"Waiting {wait_seconds}s before trying again..."))
            time.sleep(wait_seconds)
            total_waited += wait_seconds
            wait_seconds = min(wait_seconds * 2, 600)
            continue
        except Exception as e:
            return False, None, str(e)


def call_firecrawl_map(url, api_key, timeout=90):
    """Calls the Firecrawl API (the /v2/map endpoint) to discover ALL the links of a
    site quickly (usually a few seconds, even on large sites).
    The Firecrawl server is the one that accesses the site, not your PC -- so it
    works even on sites that block bots/direct crawling (like one that used to
    return 403 before).
    It costs 1 credit per call, no matter how many links come back.
    Returns a set of URLs. Raises an exception with a clear message on error.
    """
    api_url = "https://api.firecrawl.dev/v2/map"
    payload = json.dumps({"url": url}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        if e.code == 401:
            raise RuntimeError(
                "Firecrawl API key is invalid or not configured (error 401). "
                "Check firecrawl_api_key in config.json."
            )
        if e.code == 402:
            raise RuntimeError(
                "Firecrawl credits are used up (error 402). Check your balance "
                "at https://www.firecrawl.dev"
            )
        raise RuntimeError(f"Firecrawl returned HTTP error {e.code}: {detail[:300]}")
    except Exception as e:
        raise RuntimeError(f"Failed to connect to the Firecrawl API: {e}")

    data = json.loads(raw)
    if not data.get("success", True) and data.get("status") not in ("success", None):
        raise RuntimeError(f"Firecrawl returned an error: {data}")

    links = data.get("links", [])
    urls = set()
    for item in links:
        if isinstance(item, dict):
            u = item.get("url")
        else:
            u = item  # in case the API returns plain strings instead of objects
        if u:
            urls.add(u)
    return urls


def load_urls_from_file(filepath):
    """Reads URLs from a CSV or TXT file. It accepts:
    - One URL per line (plain txt)
    - CSV with a header row, using the first column that looks like it holds URLs
      (it looks for 'url', 'link', 'href' in the column name; if none is found,
      it falls back to reading the first column of every row)
    """
    import csv as csv_module
    urls = set()
    with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
        sample = f.read(4096)
        f.seek(0)
        is_csv = "," in sample.splitlines()[0] if sample.splitlines() else False

        if filepath.lower().endswith(".csv") or is_csv:
            reader = csv_module.reader(f)
            rows = list(reader)
            if not rows:
                return urls
            header = [h.strip().lower() for h in rows[0]]
            url_col = None
            for i, h in enumerate(header):
                if any(k in h for k in ("url", "link", "href")):
                    url_col = i
                    break
            start_row = 0
            if url_col is None:
                # no recognizable header -- assume column 0 and do not skip
                # the first row (it may be real data, not a header)
                url_col = 0
                if not rows[0][0].strip().lower().startswith("http"):
                    start_row = 1  # the first row did look like a header, just without an obvious name
            else:
                start_row = 1  # found a named header, skip it
            for row in rows[start_row:]:
                if len(row) > url_col:
                    val = row[url_col].strip()
                    if val.startswith("http"):
                        urls.add(val)
        else:
            for line in f:
                val = line.strip()
                if val.startswith("http"):
                    urls.add(val)
    return urls


# ---------------------------------------------------------------------------
# TIME LOG per site: when it started, when it finished, and each run.
# It lives in the site's folder: timeline_<site>.txt (to read) and
# timeline_<site>.json (for the script). It survives multiple runs (daily limit,
# app closed, etc.).
# ---------------------------------------------------------------------------
_TL = None  # state of the time log for the current run


def _fmt_dt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_dur(sec):
    sec = int(max(0, sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if d or h:
        parts.append(f"{h:02d}h")
    parts.append(f"{m:02d}min")
    if not d and not h:
        parts.append(f"{s:02d}s")
    return " ".join(parts)


_STATUS_TXT = {
    "completed": "completed (everything saved)",
    "paused_daily_limit": "paused (Internet Archive daily bandwidth limit)",
    "interrupted": "interrupted (closed / Ctrl+C / error) - can be resumed",
    "nothing_new": "nothing new to save",
    "no_urls": "no URLs found",
    "running": "running",
}


def timeline_write():
    """Writes timeline_<site>.json and timeline_<site>.txt from _TL."""
    if not _TL:
        return
    d = _TL["data"]
    try:
        with open(_TL["json_path"], "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        lines = [f"ARCHIVING TIMELINE - {d['site']}", "=" * 60]
        first = d.get("first_start_ts")
        fin = d.get("finished_ts")
        est = " (estimated from the old log)" if d.get("first_start_estimated") else ""
        lines.append(f"Started  : {_fmt_dt(first)}{est}" if first else "Started  : -")
        if fin:
            lines.append(f"Finished : {_fmt_dt(fin)}" + (" (estimated)" if d.get("finished_estimated") else ""))
            if first:
                lines.append(f"Total duration (start to finish): {_fmt_dur(fin - first)}")
        else:
            lines.append("Finished : not finished yet")
        active = sum(max(0, (r.get("end_ts") or r["start_ts"]) - r["start_ts"]) for r in d["runs"])
        lines.append(f"Active time (sum of all runs): {_fmt_dur(active)}")
        if d.get("ok_total") is not None:
            lines.append(f"Saved successfully: {d['ok_total']}/{d.get('total')} (cumulative)")
        lines += ["", "Runs:"]
        for i, r in enumerate(d["runs"], 1):
            end = _fmt_dt(r["end_ts"]) if r.get("end_ts") else "..."
            dur = _fmt_dur((r.get("end_ts") or r["start_ts"]) - r["start_ts"])
            lines.append(f"  {i}. {_fmt_dt(r['start_ts'])} -> {end}  [{dur}]  "
                         f"{_STATUS_TXT.get(r.get('status'), r.get('status'))}")
        with open(_TL["txt_path"], "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(c_warn(f"  (could not write the time log: {e})"))


def timeline_start(site_dir, slug, site_base, save_log_file):
    """Opens a new run in the site's time log (creating the log if it does not exist)."""
    global _TL
    json_path = os.path.join(site_dir, f"timeline_{slug}.json")
    txt_path = os.path.join(site_dir, f"timeline_{slug}.txt")
    data = None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    now = int(time.time())
    if not data:
        data = {"site": site_base, "first_start_ts": now, "finished_ts": None, "runs": []}
        # Site already saved BEFORE this feature existed: estimate the start from the old log.
        try:
            with open(save_log_file, "r", encoding="utf-8") as f:
                old_ts = [r["ts"] for r in json.load(f) if r.get("ts")]
            if old_ts:
                data["first_start_ts"] = min(old_ts)
                data["first_start_estimated"] = True
        except Exception:
            pass
    data["runs"].append({"start_ts": now, "end_ts": None, "status": "running"})
    _TL = {"data": data, "json_path": json_path, "txt_path": txt_path,
           "status": "interrupted", "save_log_file": save_log_file}
    timeline_write()


def timeline_end():
    """Closes the current run. The status comes from _TL['status'] (default 'interrupted',
    which covers Ctrl+C / an error / closing the terminal when there is time to get here)."""
    global _TL
    if not _TL:
        return
    d = _TL["data"]
    now = int(time.time())
    status = _TL["status"]
    d["runs"][-1]["end_ts"] = now
    d["runs"][-1]["status"] = status
    if status == "completed":
        d["finished_ts"] = now
        d.pop("finished_estimated", None)
    elif status == "nothing_new" and not d.get("finished_ts"):
        # nothing to do and a finish time was never recorded: use the last save in the log as an estimated finish
        try:
            with open(_TL["save_log_file"], "r", encoding="utf-8") as f:
                ts = [r["ts"] for r in json.load(f) if r.get("ts")]
            if ts:
                d["finished_ts"] = max(ts)
                d["finished_estimated"] = True
        except Exception:
            pass
    elif status == "paused_daily_limit":
        d["finished_ts"] = None  # there is still pending work
        d.pop("finished_estimated", None)
    # "interrupted": keeps the previous finish time (if there was one); only 'completed' changes the finish.
    timeline_write()
    print(c_dim(f"Time log: {_TL['txt_path']}"))
    _TL = None


def process_site():
    """Wraps the processing of one site in the time log (start/finish)."""
    try:
        _process_site_inner()
    except ArchiveStalled as e:
        print(c_err("\nThe Archive accepted the requests but is not finishing them, so the run "
                    "was paused."))
        print(c_warn(f"  {e}"))
        print(c_warn("  Nothing was lost and nothing was marked as failed: the waiting jobs are kept. "
                     "The Archive is probably busy. Run this script again later (in an hour or more) "
                     "and it will pick up from here."))
    finally:
        timeline_end()


def _process_site_inner():
    """Processes ONE site: asks for the URL, maps it, saves it. Returns when done."""
    if not HAS_BS4:
        print(c_err("ERROR: dependencies are missing. Run:"))
        print(c_err("  pip install beautifulsoup4 lxml"))
        return

    if not keys_are_set() and not first_run_setup():
        print(c_err("ERROR: your Internet Archive keys are not set."))
        print(c_err("Run this script in a terminal to enter them, or fill in config.json "
                    "by hand (see config.example.json)."))
        print(c_err("Get your keys at: https://archive.org/account/s3.php"))
        return

    # Ask until the answer looks like a site address. An empty answer (or a stray letter)
    # would otherwise create files with no site name inside the script's own folder.
    while True:
        site_base = input(c_info("Paste the site URL here (e.g. https://example.com): ")).strip()
        site_base = site_base.rstrip("/")
        if not site_base.startswith("http"):
            site_base = "https://" + site_base
        _host = urllib.parse.urlparse(site_base).netloc
        if "." in _host and not any(ch.isspace() for ch in site_base):
            break
        print(c_err("That does not look like a site address. "
                    "Type something like example.com or https://example.com/section"))

    mode = input(
        c_info("How should the pages be mapped?"
               "\n  [1] Use the sitemap if there is one (fastest)"
               "\n  [2] Force a manual crawl (slower, may find pages that are not in the sitemap)"
               "\n  [3] Only save this URL (or paste a manual list), without trying to open "
               "the site to discover links -- useful if the site blocks direct/bot access "
               "(e.g. error 403)"
               "\n  [4] Firecrawl (API) -- discovers all the links of the site automatically, "
               "without your PC having to access the site (great for sites that block bots). "
               "If no API key is configured, it falls back to importing a ready-made CSV/TXT "
               "file (e.g. exported by hand from the Firecrawl website)"
               "\nChoose [1/2/3/4] (Enter = 1): ")
    ).strip()
    force_crawl = (mode == "2")
    manual_urls_mode = (mode == "3")
    file_import_mode = (mode == "4")

    # Log specific to each site/section, so the history of different links is not mixed.
    # It uses domain + path (not only the domain), otherwise two different repositories
    # of the same site (e.g. github.com/user1/repoA and github.com/user2/repoB) would
    # end up in the SAME log file and overwrite each other.
    _parsed_slug = urllib.parse.urlparse(site_base)
    _slug_raw = (_parsed_slug.netloc + _parsed_slug.path).strip("/")
    domain_slug = re.sub(r"[^a-zA-Z0-9]+", "_", _slug_raw).strip("_")

    # Each site/section has ITS OWN FOLDER, created next to this script (it does not
    # depend on where the terminal/Termux was opened). All of the site's files live in there.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    site_dir = os.path.join(script_dir, domain_slug)
    os.makedirs(site_dir, exist_ok=True)
    print(c_info(f"Folder for this site: {site_dir}"))

    save_log_file = os.path.join(site_dir, f"saved_urls_{domain_slug}.json")
    url_list_cache_file = os.path.join(site_dir, f"url_list_{domain_slug}.json")
    crawl_state_file = os.path.join(site_dir, f"crawl_state_{domain_slug}.json")
    pending_jobs_file = os.path.join(site_dir, f"pending_jobs_{domain_slug}.json")
    firecrawl_cache_file = os.path.join(site_dir, f"firecrawl_map_{domain_slug}.json")

    # Start/finish time log for this site (timeline_<site>.txt in the site's folder)
    timeline_start(site_dir, domain_slug, site_base, save_log_file)

    # MIGRATION: files from old runs used to be left loose (in the script's folder or
    # in the folder where the terminal was open). If they exist and the equivalent
    # does not exist yet in the new folder, move them there -- so resuming keeps working.
    for _dest in (save_log_file, url_list_cache_file, crawl_state_file,
                  pending_jobs_file, firecrawl_cache_file):
        if os.path.exists(_dest):
            continue
        for _old_dir in (script_dir, os.getcwd()):
            _old = os.path.join(_old_dir, os.path.basename(_dest))
            if os.path.isfile(_old):
                try:
                    shutil.move(_old, _dest)
                    print(c_info(f"  -> old file moved to the site's folder: "
                          f"{os.path.basename(_dest)}"))
                except Exception as e:
                    print(c_warn(f"  Could not move {os.path.basename(_dest)}: {e}"))
                break

    # RESUME: if a log for this site already exists, load what was already saved
    # successfully before, so work is not repeated (e.g. the script was interrupted midway)
    results = []
    already_ok = set()
    try:
        with open(save_log_file, "r", encoding="utf-8") as f:
            results = json.load(f)
            # time of the last successful save of each URL (old logs without "ts" = 0)
            latest_ok = {}
            for r in results:
                if r.get("ok"):
                    latest_ok[r["url"]] = max(latest_ok.get(r["url"], 0), r.get("ts", 0))
            already_ok = set(latest_ok)
        if already_ok:
            print(c_info(f"Log found: {len(already_ok)} URLs were already saved successfully "
                  f"before. They will be skipped."))
        if already_ok and RESAVE_FILES_AFTER_DAYS > 0:
            # Files (PDF/.exe/.zip...) can be REPLACED on the server while keeping the
            # same URL -- so the ones saved more than RESAVE_FILES_AFTER_DAYS days ago
            # are taken out of already_ok and saved again (which creates a new snapshot).
            cutoff = time.time() - RESAVE_FILES_AFTER_DAYS * 86400
            stale_files = {u for u in already_ok
                           if latest_ok[u] < cutoff and is_saveable_file(urllib.parse.urlparse(u))}
            if stale_files:
                already_ok -= stale_files
                print(c_info(f"  -> {len(stale_files)} file(s) (PDF/installer/zip) saved more than "
                      f"{RESAVE_FILES_AFTER_DAYS} days ago will be saved AGAIN "
                      f"(the file may have been replaced at the same link)."))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(c_warn(f"Warning: could not read the previous log ({e}). Starting from scratch."))

    urls = None

    if file_import_mode:
        # Tries the Firecrawl API (/v2/map) first -- it discovers all the links of
        # the site on its own, in seconds, without your PC having to access the
        # site (great for sites that block bots, like 403/WAF). It only falls back
        # to importing a CSV/TXT file if the API key is not configured or if the
        # call fails for some reason (no credits, network error...).
        firecrawl_ok = False
        if not _is_unset(FIRECRAWL_API_KEY):
            # CACHE: if we already mapped this site before, ask whether to reuse it
            # (for free) instead of calling the API again (which costs 1 credit
            # per call, even if the links are already known).
            cached_urls = None
            cached_date = None
            if os.path.isfile(firecrawl_cache_file):
                try:
                    with open(firecrawl_cache_file, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
                    cached_urls = cache_data.get("urls", [])
                    cached_date = cache_data.get("saved_at", "unknown date")
                except Exception:
                    cached_urls = None

            use_cache = False
            if cached_urls:
                resp = input(
                    c_info(f"A saved mapping of this site already exists in the cache "
                           f"({len(cached_urls)} URLs, made on {cached_date}). "
                           f"[U] Use the cache (free)  [N] Fetch again from the API "
                           f"(costs 1 credit) -- Choose [U/N] (Enter = U): ")
                ).strip().lower()
                use_cache = resp in ("", "u", "use", "usar")

            if use_cache:
                urls = set(cached_urls)
                print(c_ok(f"Using {len(urls)} URL(s) from the local cache (no credits spent)."))
                firecrawl_ok = True
            else:
                print(c_info(f"Calling the Firecrawl API to map {site_base} ... "
                      f"(it usually takes only a few seconds)"))
                try:
                    urls = call_firecrawl_map(site_base, FIRECRAWL_API_KEY)
                    if urls:
                        print(c_ok(f"Firecrawl found {len(urls)} URL(s) on the site."))
                        firecrawl_ok = True
                        # Saves to the local cache so no credit has to be spent again
                        try:
                            with open(firecrawl_cache_file, "w", encoding="utf-8") as f:
                                json.dump({
                                    "urls": sorted(urls),
                                    "saved_at": time.strftime("%Y-%m-%d %H:%M"),
                                }, f, ensure_ascii=False, indent=2)
                        except Exception as e:
                            print(c_warn(f"Could not save the local cache: {e}"))
                    else:
                        print(c_warn("Firecrawl did not return any link for this URL."))
                except RuntimeError as e:
                    print(c_err(f"Firecrawl failed: {e}"))
                    print(c_warn("Falling back to importing a CSV/TXT file manually..."))
        else:
            print(c_warn("FIRECRAWL_API_KEY is not configured -- "
                  "using the manual CSV/TXT file import mode."))

        if not firecrawl_ok:
            # Imports URLs from a CSV/TXT file generated elsewhere (e.g. Firecrawl
            # through its website, Screaming Frog, or any tool that already mapped
            # the site). It does not access the site to discover anything -- it
            # only reads the file.
            #
            # First it tries to find, BY ITSELF, a file with a common name in the
            # same folder where the script is running (useful on a phone/Termux,
            # where typing a full path is a pain). It only asks you to type the path
            # if it does not find any candidate automatically.
            # (it looks in the site's own folder first, then in the script's folder)
            candidates = ["urls.csv", "url.csv", "urls.txt", "url_list.txt"]
            found_path = None
            for folder in (site_dir, script_dir):
                for file_name in candidates:
                    candidate_path = os.path.join(folder, file_name)
                    if os.path.isfile(candidate_path):
                        found_path = candidate_path
                        break
                if found_path:
                    break

            if found_path:
                use_it = input(
                    c_info(f"Found the file '{os.path.basename(found_path)}' in the same folder "
                           f"as the script. Use it? [Y/n] (Enter = yes): ")
                ).strip().lower()
                if use_it in ("", "s", "sim", "y", "yes"):
                    filepath = found_path
                else:
                    filepath = input(
                        c_info("Paste the full path of the CSV/TXT file with the URLs: ")
                    ).strip().strip('"').strip("'")
            else:
                print(c_warn(f"Could not find any file with a common name "
                      f"({', '.join(candidates)}) in the script's folder."))
                filepath = input(
                    c_info("Paste the full path of the CSV/TXT file with the URLs "
                           "(e.g. /storage/shared/Wayback/urls.csv): ")
                ).strip().strip('"').strip("'")

            try:
                urls = load_urls_from_file(filepath)
                if not urls:
                    print(c_err("No valid URL found in that file. "
                          "Check that the file has full URLs (starting with http)."))
                    _TL["status"] = "no_urls"
                    return
                print(c_ok(f"{len(urls)} unique URL(s) imported from the file."))
            except FileNotFoundError:
                print(c_err(f"File not found: {filepath}"))
                _TL["status"] = "no_urls"
                return
            except Exception as e:
                print(c_err(f"Error reading the file: {e}"))
                _TL["status"] = "no_urls"
                return
    elif manual_urls_mode:
        # Manual mode: it does not try to access the site to discover links (this
        # avoids the 403 on sites with anti-bot protection such as Cloudflare). The
        # saving itself still goes through web.archive.org/save/, where it is the
        # INTERNET ARCHIVE'S SERVER that accesses the site -- not your PC -- so a
        # local block does not affect that part.
        manual_set = {site_base}

        # Many forums (XenForo, vBulletin -- e.g. ResetEra) have pagination with a
        # predictable pattern: .../thread-name.123456/page-2, page-3, etc.
        # If you know the total number of pages, we GENERATE all the URLs right away,
        # without accessing the site even once to find that out.
        total_pages_raw = input(
            c_info("Is this URL a PAGINATED THREAD/TOPIC (like a forum) and do you "
                   "know the total number of pages? If so, type the number (e.g. 188). "
                   "If not, leave it blank and press Enter: ")
        ).strip()

        if total_pages_raw:
            try:
                total_pages = int(total_pages_raw)
                if total_pages > 1:
                    pattern = input(
                        c_info("URL pattern from page 2 onwards -- usually it is "
                               "'page-N' (XenForo/ResetEra) or 'page/N' (vBulletin/others). "
                               "Press Enter to use 'page-N' (the most common pattern): ")
                    ).strip() or "page-N"
                    for n in range(2, total_pages + 1):
                        suffix = pattern.replace("N", str(n))
                        manual_set.add(f"{site_base}/{suffix}")
                    print(c_ok(f"Generated {total_pages - 1} additional page URLs "
                          f"(page 1 through {total_pages}), using the pattern '{pattern}'."))
            except ValueError:
                print(c_warn("Invalid number, ignoring the automatic page generation."))

        print(c_info("Want to paste any more individual URLs (one per line)? "
              "Leave it blank and press Enter to finish:"))
        while True:
            extra = input("> ").strip()
            if not extra:
                break
            if not extra.startswith("http"):
                extra = "https://" + extra
            manual_set.add(extra)
        urls = manual_set
        print(c_info(f"{len(urls)} URL(s) ready to save, without going through crawl/sitemap."))
    elif force_crawl:
        # the user explicitly asked to ignore any cache and go straight to the
        # site to do the manual crawl again
        print(c_info("Chosen mode: forced manual crawl (ignoring cache and sitemap)."))
        urls = crawl_site(site_base, MAX_PAGES, state_file=crawl_state_file)
    else:
        # If a URL list saved from a previous run already exists, use it directly
        # -- it does not try to access the site again (sitemap/crawl), avoiding
        # repeated network timeouts and URLs lost to a one-off connection failure
        try:
            with open(url_list_cache_file, "r", encoding="utf-8") as f:
                urls = set(json.load(f))
            print(c_info(
                f"URL list discovered earlier was found "
                f"({len(urls)} URLs) -- using it, without accessing the site again."
            ))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(c_warn(f"Warning: could not read the URL cache ({e}). I will rediscover them."))

        # If there is no new cache yet, but a save log from a previous run already
        # exists (with URLs that were tried, whether they succeeded or failed), reuse it
        # as the URL list as well -- this avoids accessing the site again for nothing
        if urls is None and results:
            urls = {r["url"] for r in results}
            print(c_info(
                f"Reusing {len(urls)} URLs from the previous save log "
                f"-- without accessing the site again."
            ))
            with open(url_list_cache_file, "w", encoding="utf-8") as f:
                json.dump(sorted(urls), f, ensure_ascii=False, indent=2)

        if urls is None:
            urls = try_sitemaps(site_base)
            if not urls:
                urls = crawl_site(site_base, MAX_PAGES, state_file=crawl_state_file)

    # saves/updates the complete discovered list, to reuse in future runs
    # without needing to access the site again
    if urls:
        with open(url_list_cache_file, "w", encoding="utf-8") as f:
            json.dump(sorted(urls), f, ensure_ascii=False, indent=2)

    if URL_INCLUDE_PATTERN:
        before = len(urls)
        pattern = re.compile(URL_INCLUDE_PATTERN)
        urls = {u for u in urls if pattern.search(u)}
        removed = before - len(urls)
        print(c_info(f"Pattern filter ({URL_INCLUDE_PATTERN}): {removed} URLs outside the "
              f"pattern were discarded, {len(urls)} remain."))

    if DEDUPE_BY_BASE_URL:
        before = len(urls)
        urls = dedupe_by_base_url(urls)
        removed = before - len(urls)
        if removed:
            print(c_info(f"Duplicate filter: {removed} URLs removed "
                  f"(they were parameter variants of the same page)."))

    urls = sorted(urls)
    pending_urls = [u for u in urls if u not in already_ok]

    if SKIP_IF_SAVED_WITHIN_DAYS > 0 and pending_urls:
        print(c_info(f"\nChecking which ones already have a snapshot newer than "
              f"{SKIP_IF_SAVED_WITHIN_DAYS} days (this can take a while)..."))
        pending_urls = already_saved_recently_batch(
            pending_urls, days=SKIP_IF_SAVED_WITHIN_DAYS, max_workers=CDX_CHECK_PARALLEL_WORKERS
        )

    # Send order: pages first, then light files (PDFs, documents...), and last the
    # HEAVY ones (installers, archives). If the Internet Archive's daily bandwidth
    # quota runs out midway, the site's content was already saved and only the big
    # files are left for the next run.
    def send_priority(url):
        p = urllib.parse.urlparse(url)
        if not is_saveable_file(p):
            return 0
        name = p.path.lower().rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        return 1 if ext in SAVE_LIGHT_EXTENSIONS else 2

    pending_urls = sorted(pending_urls, key=lambda u: (send_priority(u), u))

    if MAX_FILE_SIZE_MB > 0 and pending_urls:
        pending_urls = skip_oversized(
            pending_urls, MAX_FILE_SIZE_MB,
            os.path.join(site_dir, f"file_sizes_{domain_slug}.json"),
            os.path.join(site_dir, f"skipped_too_big_{domain_slug}.json"),
        )

    print(c_title(f"\nTotal URLs found: {len(urls)}"))
    print(c_dim(f"Already saved before / recent (skipped): {len(urls) - len(pending_urls)}"))
    print(c_ok(f"Left to save now: {len(pending_urls)}\n"))

    if not urls:
        print(c_err("No URLs found. Check that the site URL is correct."))
        _TL["status"] = "no_urls"
        return

    if not pending_urls:
        print(c_ok("All the URLs were already saved before. Nothing to do."))
        _TL["status"] = "nothing_new"
        return

    total_pending = len(pending_urls)

    # If a previous run was interrupted midway through queueing (PC turned off,
    # terminal closed, etc.), the jobs that had already been accepted by Save Page
    # Now but not yet confirmed are kept here -- instead of re-queueing those URLs
    # from scratch (duplicating work the Internet Archive already accepted), we load
    # the job_ids and send them straight to verification.
    pending_jobs_on_disk = []
    if os.path.isfile(pending_jobs_file):
        try:
            with open(pending_jobs_file, "r", encoding="utf-8") as f:
                pending_jobs_on_disk = json.load(f)
        except Exception:
            pending_jobs_on_disk = []
        if pending_jobs_on_disk:
            urls_before_resume = list(pending_urls)
            leftover_urls = {j["url"] for j in pending_jobs_on_disk}
            pending_urls = [u for u in pending_urls if u not in leftover_urls]
            print(c_info(f"Found {len(pending_jobs_on_disk)} queued job(s) from a previous run "
                  f"that was interrupted before confirming -- I will go straight to checking "
                  f"their status, instead of queueing them again.\n"))

            # Jobs that were accepted long ago but are STILL "pending" are stuck: the Archive
            # normally finishes a capture in minutes. Checking them again only wastes hours,
            # so check a small sample and offer to send those URLs again instead.
            age_hours = (time.time() - os.path.getmtime(pending_jobs_file)) / 3600
            if age_hours >= SPN_STUCK_JOB_HOURS:
                print(c_info(f"These jobs are about {age_hours:.0f} h old. Checking a small sample "
                             f"to see whether the Archive is still working on them..."))
                stuck = jobs_look_stuck(pending_jobs_on_disk, ACCESS_KEY, SECRET_KEY)
                if stuck:
                    print(c_warn("\nEvery job in the sample is still 'pending' at the Archive after "
                                 "hours, so they look stuck and will probably never finish."))
                    acct = get_user_status(ACCESS_KEY, SECRET_KEY)
                    if acct and acct.get("processing") == 0:
                        print(c_warn("  The Archive also says it is not processing any of your "
                                     "requests right now (processing = 0)."))
                    try:
                        answer = input(c_info(f"Drop these {len(pending_jobs_on_disk)} jobs and send "
                                              f"their URLs again? [Y/n]: ")).strip().lower()
                    except EOFError:
                        answer = "n"
                    if answer in ("", "y", "yes", "s", "sim"):
                        pending_jobs_on_disk = []
                        pending_urls = urls_before_resume
                        try:
                            os.remove(pending_jobs_file)
                        except OSError:
                            pass
                        print(c_ok(f"Dropped. {len(pending_urls)} URL(s) will be queued again.\n"))

    def save_pending_jobs():
        with open(pending_jobs_file, "w", encoding="utf-8") as f:
            json.dump(pending_jobs_on_disk, f, ensure_ascii=False, indent=2)

    already_persisted_job_ids = set()
    early_failed = []   # URLs whose job ended in an error while we were still sending
    progress = {"last": time.time(), "started": time.time(), "confirmed": 0, "failed": 0, "asked": 0}

    def on_job_resolved(item, ok, info):
        """Called the moment each job is confirmed (success or a final error).
        Writes the result to results/save_log_file IMMEDIATELY (instead of waiting
        for the whole batch of thousands of jobs to finish verifying), and also
        removes the job from pending_jobs_file -- if the script is interrupted in the
        middle of verification, what was already confirmed up to that point is not lost."""
        results.append({"url": item["url"], "status": item["status"], "ok": ok, "detail": info,
                        "ts": int(time.time())})
        already_persisted_job_ids.add(item["job_id"])
        with open(save_log_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        pending_jobs_on_disk[:] = [j for j in pending_jobs_on_disk if j["job_id"] != item["job_id"]]
        save_pending_jobs()

    def reap(max_checks=None):
        """Checks the OLDEST waiting jobs once and records the ones that finished.
        Returns how many finished. This runs while we keep sending, so a Archive that
        accepts requests but does not finish them is noticed in minutes, not hours."""
        max_checks = max_checks or SPN_REAP_CHECKS
        finished = 0
        error_streak = 0
        reasons = {}
        for item in list(pending_jobs_on_disk[:max_checks]):
            is_resolved, ok, info = check_job_status_once(item["job_id"], ACCESS_KEY, SECRET_KEY)
            if is_resolved:
                on_job_resolved(item, ok, info)
                finished += 1
                error_streak = 0
                if ok:
                    progress["confirmed"] += 1
                    print(c_ok("  confirmed OK ✓ ") + item["url"])
                else:
                    progress["failed"] += 1
                    early_failed.append(item["url"])
                    print(c_err(f"  FAILED ✗ ({info}) ") + item["url"])
            elif info.startswith("error checking status"):
                error_streak += 1
                reasons[info] = reasons.get(info, 0) + 1
                if error_streak >= SPN_VERIFY_ERROR_STREAK:
                    break  # the Archive is refusing the checks right now; try again later
            else:
                error_streak = 0
                _note_status_message(info)
            time.sleep(1)
        if finished:
            progress["last"] = time.time()   # any job that ended, saved or failed, means the Archive is moving
        for reason, count in reasons.items():
            print(c_warn(f"  !! {count} x {reason}"))
        minutes = (time.time() - progress["started"]) / 60
        rate = f", about {progress['confirmed'] / minutes:.1f}/min" if minutes >= 1 and progress["confirmed"] else ""
        failed_note = f", {progress['failed']} failed" if progress["failed"] else ""
        print(c_dim(f"     .. {len(pending_jobs_on_disk)} accepted job(s) waiting for confirmation, "
                    f"{progress['confirmed']} confirmed in this run{failed_note}{rate}"))
        return finished

    def check_stall():
        """Raises ArchiveStalled when jobs are waiting but the Archive is not finishing them.

        After SPN_STALL_CHECK_MINUTES with no job finishing, it asks the Archive how busy
        your account is: processing = 0 means it is not working on any of your requests
        (stalled, stop now); processing > 0 means it is working, just slowly (keep waiting).
        If nothing finishes for SPN_STALL_MINUTES it stops either way."""
        if not pending_jobs_on_disk:
            return
        idle = time.time() - progress["last"]
        if idle >= SPN_STALL_CHECK_MINUTES * 60 and \
                time.time() - progress["asked"] >= SPN_STALL_CHECK_MINUTES * 60:
            progress["asked"] = time.time()
            st = get_user_status(ACCESS_KEY, SECRET_KEY)
            if st is not None and st.get("processing") == 0:
                raise ArchiveStalled(
                    f"{len(pending_jobs_on_disk)} job(s) are waiting, no job finished for "
                    f"{int(idle // 60)} minutes, and the Archive says it is not processing any of "
                    f"your requests right now (processing = 0).")
            if st is not None:
                print(c_dim(f"     .. no job finished for {int(idle // 60)} min, but the Archive is "
                            f"working on {st.get('processing')} of your requests: just slow, waiting."))
        if idle >= SPN_STALL_MINUTES * 60:
            raise ArchiveStalled(f"{len(pending_jobs_on_disk)} job(s) are waiting and none finished "
                                 f"for {int(idle // 60)} minutes.")

    def wait_for_room():
        """Never lets more than SPN_MAX_IN_FLIGHT accepted-but-unconfirmed jobs pile up.
        If the Archive is behind, we simply send fewer requests, and wait, without
        marking anything as failed and without sending the same URL twice."""
        while len(pending_jobs_on_disk) >= SPN_MAX_IN_FLIGHT:
            reap()
            if len(pending_jobs_on_disk) < SPN_MAX_IN_FLIGHT:
                break
            check_stall()
            print(c_dim(f"     .. the Archive is behind ({len(pending_jobs_on_disk)} jobs waiting, "
                        f"limit {SPN_MAX_IN_FLIGHT}). Not sending more until some finish."))
            time.sleep(SPN_POLL_INTERVAL_SECONDS)

    def submit_batch(url_batch, label="Queueing", final_pass=False):
        """Phase 1: sends the POST for each URL and collects the job_ids, without
        waiting for each one to be confirmed. URLs that fail at the POST for a
        permanent reason (e.g. a 4xx that is not a rate limit) go straight to
        results as failures. URLs that fail because of a temporary pause (e.g. a rate
        limit or host-crawling-paused that did not clear within the wait time) are
        deferred -- unless final_pass=True, in which case any failure is final (used
        in the last retry round, at the end of the batch).

        Each accepted job is written to disk right away (pending_jobs_file), so it
        is not lost if the script is interrupted before the verification phase."""
        submitted = []
        deferred = []
        for i, url in enumerate(url_batch, 1):
            wait_for_room()
            pct = int((i / len(url_batch)) * 100)
            print(c_title(f"[{i}/{len(url_batch)} - {pct}%]") + f" {label}: {url}")
            accepted, status, job_id_or_detail = enqueue_to_wayback(url, ACCESS_KEY, SECRET_KEY)
            if accepted:
                print(c_dim(f"  -> accepted by the Archive, not saved yet (job {job_id_or_detail})"))
                entry = {"url": url, "job_id": job_id_or_detail, "status": status}
                submitted.append(entry)
                if not pending_jobs_on_disk:
                    progress["last"] = time.time()  # nothing was waiting before this one
                pending_jobs_on_disk.append(entry)
                save_pending_jobs()
            else:
                is_temp_timeout = isinstance(job_id_or_detail, str) and job_id_or_detail.startswith("TIMEOUT_TEMP::")
                detail = job_id_or_detail.split("TIMEOUT_TEMP::", 1)[-1] if is_temp_timeout else job_id_or_detail
                if "max-daily-bandwidth" in str(detail):
                    # The Internet Archive's DAILY bandwidth limit was reached: trying
                    # the other URLs today only produces one failure after another (and
                    # spends 14s on each). It stops queueing; what is left is NOT
                    # written to the log as a failure, so the next run (another day)
                    # resumes exactly from here.
                    remaining = len(url_batch) - i + 1
                    print(c_warn(f"\n  !! Internet Archive DAILY bandwidth limit reached. Stopping "
                          f"the queueing for today ({remaining} URL(s) are left for the next run, "
                          f"without being marked as failures). Run the script again another day.\n"))
                    _TL["hit_daily_limit"] = True
                    break
                if is_temp_timeout and not final_pass:
                    print(c_warn(f"  -> status={status} temporary pause, leaving it to retry "
                          f"at the end of the batch ({detail})"))
                    deferred.append(url)
                else:
                    print(c_err(f"  -> status={status} FAILED TO QUEUE ✗ ({detail})"))
                    results.append({"url": url, "status": status, "ok": False, "detail": detail})
                    with open(save_log_file, "w", encoding="utf-8") as f:
                        json.dump(results, f, ensure_ascii=False, indent=2)
            time.sleep(DELAY_SECONDS)
            if i % SPN_REAP_EVERY == 0:
                reap()
                check_stall()
        return submitted, deferred

    # Show how busy the account is and how much of today's allowance is left.
    account = get_user_status(ACCESS_KEY, SECRET_KEY)
    if account:
        print(c_info(f"Archive account: {describe_user_status(account)}"))
        left = (account.get("daily_captures_limit") or 0) - (account.get("daily_captures") or 0)
        if account.get("daily_captures_limit") and len(pending_urls) > left:
            print(c_warn(f"  !! Only {left} captures are left in today's allowance, but "
                         f"{len(pending_urls)} URL(s) are waiting. The rest can go tomorrow."))

    # Jobs left over from an interrupted run: look at all of them once BEFORE sending anything new, so
    # the user sees at once what became of them (the message above promises exactly that). Without
    # this they would only be checked after the first SPN_REAP_EVERY new URLs had been sent.
    if pending_jobs_on_disk:
        waiting_before = len(pending_jobs_on_disk)
        saved_before, failed_before = progress["confirmed"], progress["failed"]
        print(c_info(f"Checking the {waiting_before} job(s) from the previous run first...\n"))
        reap(max_checks=waiting_before)
        still_waiting = len(pending_jobs_on_disk)
        print(c_info(f"Previous run: {waiting_before - still_waiting} of {waiting_before} job(s) finished "
                     f"({progress['confirmed'] - saved_before} saved, {progress['failed'] - failed_before} failed); "
                     f"{still_waiting} still waiting"
                     + (" and will be checked again while the rest is sent.\n" if still_waiting else ".\n")))

    submitted, deferred = submit_batch(pending_urls)

    if deferred:
        print(c_title(f"\n{len(deferred)} URL(s) were temporarily paused (e.g. host-crawling-paused) "
              f"during the batch -- trying again now, one last time...\n"))
        submitted_retry, _ = submit_batch(deferred, label="Retrying (after pause)", final_pass=True)
        submitted.extend(submitted_retry)

    # From here on, whatever was loaded from an interrupted run (if any) is
    # included -- those jobs go into verification together with the ones from this run.
    submitted = pending_jobs_on_disk

    # Jobs that ended in an error while we were still sending were already logged as
    # failures; send those URLs once more now (one extra attempt, like the retry step below).
    if early_failed:
        retry_urls = list(dict.fromkeys(early_failed))
        early_failed.clear()
        print(c_warn(f"\n{len(retry_urls)} URL(s) ended in an error while sending -- trying them "
                     f"again now...\n"))
        submit_batch(retry_urls, label="Retrying (failed earlier)", final_pass=True)

    # A COPY: on_job_resolved removes finished jobs from pending_jobs_on_disk while we verify,
    # and the retry step below still needs to see the ones that failed.
    remaining_to_verify = list(pending_jobs_on_disk)

    for attempt in range(SPN_RETRY_ATTEMPTS + 1):
        if not remaining_to_verify:
            break

        label = "Verifying" if attempt == 0 else f"Verifying (attempt {attempt + 1})"
        print(c_title(f"\n--- {label}: {len(remaining_to_verify)} job(s) ---\n"))
        try:
            job_results = verify_all_jobs(remaining_to_verify, ACCESS_KEY, SECRET_KEY,
                                           on_resolved=on_job_resolved)
        except VerificationBlocked as e:
            print(c_err("\nThe Archive keeps refusing the status checks, so these jobs could not "
                        "be verified for now."))
            print(c_err(f"  Last error: {e}"))
            print(c_warn("  Nothing was lost: the unconfirmed jobs are kept in the pending_jobs "
                         "file. Run this script again later (in an hour or more) and it will "
                         "pick up from here. Nothing was marked as failed."))
            return

        failed_items = []
        for item in remaining_to_verify:
            if item["job_id"] in already_persisted_job_ids:
                # Already written right away by the callback (success or a final
                # error from the API itself) -- it only needs to go into
                # failed_items if it failed, to decide whether to resend it.
                ok, info = job_results.get(item["job_id"], (False, "no response"))
                if not ok:
                    failed_items.append(item)
                continue

            ok, info = job_results.get(item["job_id"], (False, "no response"))
            if ok:
                results.append({"url": item["url"], "status": item["status"], "ok": True, "detail": info,
                                "ts": int(time.time())})
            else:
                failed_items.append(item)
                if attempt == SPN_RETRY_ATTEMPTS:
                    # last attempt allowed. Before giving up for good, do a final
                    # check on the CDX API: it is possible that an EARLIER attempt
                    # (a different job_id, from a past retry round) actually managed
                    # to save it, even though this last job failed for another
                    # reason -- without this, a real success from an earlier attempt
                    # would be masked by the error of the last one.
                    recently_saved = already_saved_recently(item["url"], days=1)
                    if recently_saved:
                        print(c_ok(f"     .. final CDX check: a recent snapshot already exists "
                              f"for {item['url']} (probably from an earlier attempt) -> OK ✓"))
                        results.append({"url": item["url"], "status": item["status"], "ok": True,
                                         "detail": f"confirmed via CDX even though the last job failed ({info})",
                                         "ts": int(time.time())})
                    else:
                        results.append({"url": item["url"], "status": item["status"], "ok": False, "detail": info})

        with open(save_log_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        if not failed_items:
            break

        if attempt < SPN_RETRY_ATTEMPTS:
            print(c_warn(f"\n{len(failed_items)} URL(s) failed or were not confirmed. "
                  f"Resending (attempt {attempt + 2}/{SPN_RETRY_ATTEMPTS + 1})...\n"))
            failed_urls = [item["url"] for item in failed_items]
            remaining_to_verify, _ = submit_batch(failed_urls, label="Re-queueing", final_pass=True)

    # Every job queued in this run already has a final result written to
    # save_log_file (ok=True or ok=False) -- there is no need to track any of
    # them as "pending" anymore, so the state file is cleaned up.
    if os.path.isfile(pending_jobs_file):
        try:
            os.remove(pending_jobs_file)
        except Exception:
            pass

    ok_count = sum(1 for r in results if r["ok"])
    _TL["data"]["ok_total"] = ok_count
    _TL["data"]["total"] = len(results)
    _TL["status"] = "paused_daily_limit" if _TL.get("hit_daily_limit") else "completed"
    print(c_title(f"\nFinished: {ok_count}/{len(results)} saved successfully (cumulative total)."))
    print(c_dim(f"Full log at: {save_log_file}"))


def main():
    print(c_title("=" * 60))
    print(c_title("   WAYBACK BULK SAVE"))
    print(c_title("=" * 60) + "\n")

    while True:
        process_site()

        print()  # blank line to separate visually
        again = input(
            c_info("Save another site now? [y/N]: ")
        ).strip().lower()
        if again not in ("s", "sim", "y", "yes"):
            print(c_title("\nExiting. See you next time!"))
            break
        print(c_title("\n" + "=" * 60) + "\n")


if __name__ == "__main__":
    main()
