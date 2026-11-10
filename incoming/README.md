# incoming

Progress files of a run, waiting to become a public record.

When files are added or changed in `incoming/<site_folder>/`, the workflow
[`update-records.yml`](../.github/workflows/update-records.yml) runs
[`record_archive.py`](../record_archive.py) on that folder and commits the result to
[`archived-sites/`](../archived-sites/): the lists, the README and the skyline card. Nobody has to
run anything by hand.

## What to put in a folder

Take these three files from the site's working folder (the one `wayback_bulk_save.py` creates)
and put them in `incoming/<site_folder>/`, where `<site_folder>` is the same name:

- `saved_urls_<site_folder>.json`
- `url_list_<site_folder>.json`
- `timeline_<site_folder>.json`

All three are needed: the first has what was saved, the second gives the total, the third gives
the site's address and the dates. If one is missing, the workflow stops with an error and says
which. Upload them again to refresh the record; the newest files win.

## What not to put here

- `config.json`, or anything with keys. The `.gitignore` already blocks it, but do not try.
- Anything you do not want published. **Files in this folder are public as they are**: the
  workflow does not apply `--exclude`. If a site needs `--exclude` (for example to keep user
  profile URLs out of the lists), run `record_archive.py` on your own computer instead and commit
  only the result in `archived-sites/`.
