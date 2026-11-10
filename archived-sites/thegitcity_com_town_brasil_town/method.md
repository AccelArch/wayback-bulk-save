# How the list of URLs was obtained

This site was **not** mapped with the script's own discovery modes (sitemap or crawl).
The site's `sitemap.xml` lists only 3 pages (home, ranking, levels), and the pages
that matter are not linked in a way a crawler can follow. The list was built by hand
from the site's public pages, then given to `wayback_bulk_save.py` in the "import a
file of URLs" mode. This file records exactly how, so the list can be checked or rebuilt.

## Scope and permission

- **Scope:** the public pages of one community map of the site, the Brazilian one
  (`/town/brasil-town`): the map page, its states, its cities, and the profile page of each
  developer on it.
- **Permission:** the site's owner approved, by e-mail on 2026-11-10, archiving these
  public pages and keeping the list of URLs in this repository. The maintainer keeps that
  e-mail.
- **Not included:** the other community maps of the site (39 pages) and about 690
  developers who appear only on those. Whole-site archiving was not requested from the
  owner, so it was not done. See "What is not in the list" below.

## How each kind of URL was found

The site's map page embeds, in its own page data, the list of its developers and of its
cities. That is where the URLs come from.

1. **Developer profiles** (`/dev/<username>`): the usernames in the map page's data
   (public GitHub usernames), one URL per username. 5,375 on 2026-11-10.
2. **City pages** (`/town/brasil-town?cidade=<city>`): the city entries in the same data.
   Each new city was checked before being listed: the page had to answer normally and have
   its own title, different from the map's default title (a city name the site does not
   know falls back to that default title). 1,341 on 2026-11-10.
3. **State pages** (`/town/brasil-town?uf=<state>`): one per state that has a valid city. 27.
4. **Top-level pages:** the home page, the map page, the ranking, the levels page and the
   index of community maps. 5 pages.
5. **Five group pages** of Brazilian communities listed in that index: `acelera`,
   `fulldev`, `tech-in-rio-town`, `espirito-santo` and `jequie-town`. They were classed as
   Brazilian by their names.

Total: **6,753 URLs** (5,375 + 1,341 + 27 + 5 + 5).

## When

- First list: 2026-11-05 and 2026-11-06, 6,413 URLs.
- Refreshed on 2026-11-10, because the site had grown: 307 new developers and 75 new
  cities were found. Cities already checked were not checked again.

The site keeps growing, so a developer or city that joined after 2026-11-10 is not in the
list, and a page removed since then may have been captured while it still existed.

## How the discovery requests were made

About 1,500 small requests over the two passes, spaced at least 0.15 seconds apart, plus a
few full downloads of the map page (about 9 MB). Only public pages were read, with no login.

## How the pages were saved

By the Internet Archive's own servers, through Save Page Now, using the maintainer's
account keys: one request about every 14 seconds, 3 at a time at most, as the Archive allows.
The script confirms each capture with the Archive. The URLs confirmed as saved are in
`saved-urls.txt`. The site's `robots.txt` allows general crawlers (`Allow: /`) and declares
`Content-Signal: search=yes, ai-train=no`; it also blocks a list of AI crawlers by name.
Saving pages to the Wayback Machine is not AI training.

## What is not in the list

- The 39 other community maps and the developers who appear only on them.
- Anything behind a login.
- Developers who joined after 2026-11-10.

If the owner agrees to a wider archive, those pages can be added as a second batch and
recorded here.

## Privacy

The profile URLs contain public GitHub usernames. Anyone who appears here can ask to be
removed from this record: see [Removal requests](../../README.md#removal-requests).
Removing a name here does not remove the snapshot from the Wayback Machine.
