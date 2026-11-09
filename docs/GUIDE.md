# Guide: how Wayback Bulk Save works

Read this once before your first run. It explains what the tool does, what it
cannot do, and why one of its modes uses a third-party service (Firecrawl).

---

## 1. What this tool is

The Internet Archive's **Wayback Machine** keeps dated copies ("snapshots") of web
pages. It has a button called **Save Page Now** that makes a new snapshot of a
URL on demand.

Doing that by hand works for one page. This script does it for **a whole site**:
it finds every page and downloadable file, asks the Archive to save each one, and
checks that each save really happened.

**Important:** the copy is made by the Internet Archive's servers, not by your
computer. The script never downloads the site to your disk. It only tells the
Archive "please save this URL" and then checks the result.

## 2. What you need before running it

| Requirement | Needed for | How to get it |
|---|---|---|
| **Python 3.9 or newer** | Everything | <https://www.python.org/downloads/> (check with `python --version`) |
| **beautifulsoup4** | Reading HTML pages. The script refuses to start without it | `pip install -r requirements.txt` |
| **lxml** | The HTML parser the crawler uses (modes 1 and 2) | `pip install -r requirements.txt` |
| **certifi** | Fixes `CERTIFICATE_VERIFY_FAILED` errors on some Windows and Android (Termux) setups. Recommended | `pip install -r requirements.txt` |
| **Internet Archive keys** (free) | Saving anything. Required | <https://archive.org/account/s3.php> |
| **Firecrawl API key** | Only the Firecrawl part of mode 4. Optional | <https://www.firecrawl.dev> |

Everything else the script imports (`urllib`, `json`, `ssl`, `csv`...) ships with
Python, so there is nothing more to install. One command installs all the packages:

```bash
pip install -r requirements.txt
```

The first time you run the script it asks for your keys and saves them in
`config.json` (the secret key is hidden while you type or paste it). You can also
create that file by hand from `config.example.json`.

**How your keys are handled**

- **First run:** the script asks for your Internet Archive *access key* (shown on
  screen) and *secret key* (hidden, like a password), then for an optional
  Firecrawl key (press Enter to skip).
- **Where they are stored:** in `config.json`, next to the script, on your own
  device. On Linux, macOS and Termux only your user can read the file. The keys
  are only sent to archive.org (and firecrawl.dev, if you use it) when the
  script talks to them.
- **Next runs:** it does not ask again.
- **To change a key** (a new key, a typo, adding Firecrawl later): delete
  `config.json` and run the script again, or open the file and edit it.
- **No terminal to type in** (for example a scheduled job): create `config.json`
  by hand from `config.example.json`, because the script only asks questions in
  an interactive terminal.
- **If your keys are ever exposed,** get new ones on the same page where you
  got the first ones (<https://archive.org/account/s3.php>) and replace them.
- **Never share `config.json`.** It is git-ignored, so normal commits skip it.

The script needs almost no disk space (only small log files) and little internet
data, because the Archive's servers do the heavy transfer of the actual pages and
files.

## 3. Running it on a phone (Termux, Android)

**Why it is built to run on a phone.** Archiving a big site takes hours or days.
Leaving a PC on for that long is wasteful, so the script is designed to run from
a phone with Termux, a terminal app for Android:

- **One file, few dependencies**, so it is easy to install on a phone.
- **Everything is saved as it goes** (per-site folder, state files). If Android
  kills the app, the battery dies or you close it, just start it again: it
  resumes and skips what already succeeded.
- **The site's folder is created next to the script**, not wherever the terminal
  happened to be opened, which avoids losing files on a phone.
- **Few things to type.** Prompts have defaults (just press Enter) and mode 3
  accepts a pasted list of URLs.
- **The Archive does the heavy work**, so the phone only sends small requests.

**Setup:**

1. Install **Termux from F-Droid** (<https://f-droid.org/packages/com.termux/>).
   The Google Play version is outdated and no longer maintained.
2. Install the tools. `clang`, `libxml2` and `libxslt` are only needed to build
   `lxml`:
   ```bash
   pkg update && pkg upgrade
   pkg install python git clang libxml2 libxslt
   ```
3. Get the project and install the packages (building `lxml` can take several
   minutes on a phone; that is normal):
   ```bash
   git clone https://github.com/AccelArch/wayback-bulk-save.git
   cd wayback-bulk-save
   pip install -r requirements.txt
   ```
4. Run it. The first time, it asks for your keys (paste them; the secret key stays
   hidden) and saves them, so you never have to edit a file on the phone:
   ```bash
   python wayback_bulk_save.py
   ```

**Tips for long runs:**

- Run `termux-wake-lock` (or use the "Acquire wakelock" button in the Termux
  notification) so Android does not put Termux to sleep.
- In Android's battery settings, set Termux to **unrestricted / not optimized**.
- Keep the Termux notification visible; it keeps the app alive.
- If it still gets killed, nothing is lost: start it again.
- To import a URL file stored on your phone, run `termux-setup-storage` once and
  use a path like `/storage/shared/Download/urls.txt`.

## 4. What happens when you run it

1. **Discover.** You give a site URL and choose a mode (see section 5). The script
   builds a list of URLs.
2. **Filter.** It removes duplicates, technical junk (`/api/`, `/wp-admin/`, login
   pages...), cosmetic variants (`?utm_source=...`, `?sort=...`) and files above
   the size cap.
3. **Queue.** It sends every URL to Save Page Now, one every ~14 seconds, pages
   first and heavy files last.
4. **Confirm.** Save Page Now is asynchronous: "accepted" only means "in the
   queue". The script then checks every job and prints **OK** only when the Archive
   confirms the snapshot. Failures are retried once.

Everything is written to a folder named after the site, next to the script.
If you stop it (Ctrl+C, closed terminal, daily limit), **run it again and it
continues where it stopped**. It will not redo URLs that already succeeded.

## 5. The four discovery modes

| Mode | What it does | Use it when | Needs |
|---|---|---|---|
| **1. Sitemap** (default) | Reads the site's sitemap; falls back to a crawl if there is none | Most normal sites | Nothing |
| **2. Forced crawl** | Opens the homepage, follows links page by page | The sitemap is missing pages | Nothing |
| **3. Single URL / list** | Saves only the URL(s) you give. Can generate `page-2 ... page-N` for forum threads | You already know the URLs, or the site blocks bots | Nothing |
| **4. Firecrawl / file** | Asks Firecrawl for the site's link list, or imports a `.txt`/`.csv` of URLs | The site blocks bots (403), or modes 1-2 find too little | Firecrawl key **or** a URL file |

Tip: you can restrict a crawl to one section by giving a URL with a path, for
example `https://example.com/docs`. Only pages under `/docs` are followed.

## 6. Why Firecrawl is used (and why it is optional)

Modes 1 and 2 make **your computer** open the site to find links. Some sites
refuse that: they answer `403 Forbidden` or show a bot check (Cloudflare and
similar). Then the script cannot even build the list.

Note that this only blocks *discovery*. The actual saving is done by the Archive's
servers, which usually still work.

[Firecrawl](https://www.firecrawl.dev) is a commercial service (check their site
for the current free allowance) that visits the site from **its own servers** and returns a list of links in a few
seconds. Mode 4 sends it one request (`/v2/map`) and gets that list back. That is
all it is used for. The script does not use Firecrawl to save anything.

- **Cost:** 1 Firecrawl credit per mapping call. The result is cached in the site's
  folder, so running again asks whether to reuse it for free.
- **It is optional.** No key means no Firecrawl; mode 4 then reads a URL file
  instead. You can produce that file with any tool, or by hand.
- **It is not perfect.** A map can miss some pages and include a few that are not
  real. If completeness matters, combine it with mode 1 or 2.
- Your Firecrawl key is stored in `config.json` (the first-run setup asks for it), never in the script.

## 7. What it can do

- Save every page it can find on a public site, then confirm each snapshot.
- Save **files** linked from pages: PDF, Office documents, e-books, mp3/mp4,
  installers (`.exe`, `.msi`, `.dmg`, `.apk`...), archives (`.zip`, `.7z`...), even
  when the file lives on another domain (for example a GitHub release).
- Skip files above a size cap (default 200 MB) and list what was skipped, so you
  can raise the cap later.
- Re-save files every 7 days (configurable), because a site can replace a file
  without changing its link.
- Keep going across days when the Archive's daily limits stop it.
- Record start and end times per site in `timeline_<site>.txt`.

## 8. What it cannot do

Be realistic about these before you start:

- **It cannot save private content.** Anything behind a login, a paywall or a
  private account is out of reach. The Archive's servers see what a logged-out
  visitor sees.
- **It does not guarantee a perfect copy.** Snapshots of sites that depend heavily
  on JavaScript, embedded players, maps or live data may look broken or empty when
  replayed, even though the save succeeded. "OK" means "a snapshot exists", not
  "it looks identical".
- **The crawler does not run JavaScript.** Links that only appear after scripts
  run (infinite scroll, single-page apps) are not found by modes 1-2. Use mode 4
  or give a URL list.
- **It cannot find pages nobody links to.** If a page is not in the sitemap, not
  linked, and not in your list, it is not saved.
- **It cannot bypass bot checks, CAPTCHAs or the Archive's limits.** It waits and
  retries; it does not evade anything.
- **It does not check `robots.txt`.** The Archive applies its own exclusion rules,
  and a site owner can have pages removed or excluded from the Wayback Machine.
  Those URLs will simply fail.
- **It is not instant.** At one request every ~14 seconds, about 250 URLs per
  hour; a site with thousands of pages takes many hours or days.
- **It does not create a local backup.** If you need the files on your own disk,
  use a different tool (for example `wget` or HTTrack).

## 9. The Archive's limits (and how the script reacts)

| What you see | What it means | What the script does |
|---|---|---|
| `too-many-daily-captures` | The same URL was already saved several times today (roughly 5 per day) | Reports the failure; run again tomorrow if you need another snapshot |
| `user-session-limit` | Too many captures running at once for your account | Waits and retries |
| `host-crawling-paused` | The Archive paused that site temporarily to avoid overloading it | Waits with growing delays, retries at the end |
| `429` / `5xx` | Rate limit or Archive overload | Waits 1, 2, 4... minutes (up to 10 min), then retries |
| `max-daily-bandwidth` | Your account hit the daily data cap, easy to reach with large files | Stops cleanly and records `paused_daily_limit`; run again the next day |
| Firecrawl `401` | Firecrawl key missing or wrong | Falls back to the URL-file import |
| Firecrawl `402` | Firecrawl credits are used up | Falls back to the URL-file import |

The limits above come from the Archive and change over time; the numbers are
approximate observations, not guarantees.

## 10. Checking that it worked

- The terminal prints **OK ✓** for each confirmed URL and a final
  `Finished: X/Y saved successfully`.
- `saved_urls_<site>.json` lists every URL with its result and reason for failure.
- To look at a snapshot yourself, open
  `https://web.archive.org/web/*/<your-url>` in a browser.
- `timeline_<site>.txt` shows when each run started and ended, and why it stopped
  (`completed`, `interrupted`, `paused_daily_limit`...).

If a replay looks broken in one browser, try another: heavy pages can load
slowly or fail in one browser and work in another.

## 11. Settings you may want to change

They are plain constants at the top of `wayback_bulk_save.py`:

| Setting | Default | Meaning |
|---|---|---|
| `DELAY_SECONDS` | 14 | Pause between save requests. Don't lower it much |
| `MAX_FILE_SIZE_MB` | 200 | Files above this are skipped (`0` = no cap) |
| `RESAVE_FILES_AFTER_DAYS` | 7 | Re-save files older than this (`0` = never) |
| `MAX_PAGES` | 50000 | Safety limit for the crawler |
| `URL_INCLUDE_PATTERN` | `None` | A regex; only matching URLs are saved |
| `ALLOW_EXTERNAL_FILE_LINKS` | `True` | Also save files hosted on other domains |
| `CAPTURE_SCREENSHOT` | `False` | Ask the Archive to also take a screenshot |

## 12. Rules of use (please read)

Save Page Now is a free public service run by a non-profit. Using this tool means
you accept the rules below.

**You must:**

- Archive **public** content only. Never try to save pages behind a login,
  private accounts, or content you only reached with someone else's credentials.
- Keep the default delay and run **one** copy per account. Do not lower
  `DELAY_SECONDS` to go faster and do not share your keys with other people.
- Follow the Internet Archive's
  [terms of use](https://archive.org/about/terms.php) and the terms of the site
  you are archiving.
- Keep your keys secret. They are tied to your account, and everything saved with
  them is attributed to you.

**You must not:**

- Use it to attack, overload or "stress test" a website. If a site is slow or
  returns errors, stop.
- Archive personal data, private messages, medical or financial records, or
  anything that exposes people who did not choose to be public.
- Use it to get around a paywall, a takedown, or a site owner's wish not to be
  archived.
- Use it for anything illegal in your country or in the site's country.

**Things to know before you save:**

- Snapshots are **public** on web.archive.org and very hard to remove. Treat every
  save as a publication.
- Copyright still applies. Saving a copy in the Wayback Machine does not give you,
  or anyone, rights over the content.
- Site owners can ask the Internet Archive to exclude or remove their pages
  (see their policies at archive.org). If a request reaches you, respect it.

**Disclaimer.** This project is independent and is **not affiliated with, endorsed
by, or supported by the Internet Archive or Firecrawl**. The software is provided
"as is", without warranty of any kind (see [LICENSE](../LICENSE)). The author is
not responsible for what you archive, for bans or limits applied to your account,
or for any loss or damage resulting from using it. Maintainers may refuse or
close archive requests (the Issues form) at their discretion.

**Removal requests.** If you own a site, or you are a person whose information
appears in something archived through this project, you can ask for it to be
removed. The maintainer will honor the request: the URLs and details are removed
from this repository (including the `archived-sites/` records and, for personal
data, the repository history). Open a "Removal request" issue (or contact the
maintainer through GitHub). Note that the maintainer cannot delete snapshots
that already exist on web.archive.org: those belong to the Internet Archive, and
removal from there has to be requested from the Archive itself (see its
policies at archive.org). The maintainer will not object to such a request.

Note that a snapshot can also contain information from other pages (for example,
profile data shown inside a list page), so removing a URL from this repository does
not necessarily remove that information from the Wayback Machine.
