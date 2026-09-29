# Contributing

Thanks for helping! This page covers how to set up a dev copy, what every change needs, and the rules for
adding a new event source. Those rules are strict on purpose: the project only works if the sites it reads
from are treated well.

## Set up
```sh
git clone <repo-url> && cd nymetro_eventlocator
python3 -m venv .venv && . .venv/bin/activate      # Windows: py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.lock               # exact, tested versions
pip install --no-deps -e .
pytest
ruff check .                                       # lint; `--fix` applies safe fixes
```
The tests never use the network or your real secrets. They run local HTTP servers and a throwaway secret store.
To try the tool itself, run `nymetro_eventlocator init` in a scratch folder so your real `config.yaml` and data stay untouched.

## Every change
- **Comes with tests, and the full suite passes** (`pytest`) before you move on to the next change.
  CI runs it on Linux, macOS and Windows with Python 3.12 and 3.13.
- **Keep changes small and focused.** One pull request should do one thing.
- **Match the surrounding code:** its naming, comment style and structure. Commands live in
  `nymetro_eventlocator/cli/`, one module per command; sources live in `nymetro_eventlocator/sources/`.
- **Changing dependencies?** Edit `pyproject.toml`, then run `pip install uv` (once) and `python scripts/lock.py`,
  and commit both `.lock` files with it. CI fails if the lock files are out of date.
- **Update the docs** when behavior changes: `README.md` for users, `docs/ARCHITECTURE.md` for how it works,
  and add a line under "Unreleased" in `CHANGELOG.md` for anything a user would notice.
- **CI must be green.** Besides the tests, it runs `ruff check`, and checks that the lock files are current, that no
  pinned dependency has a known vulnerability, and that no secret is anywhere in the git history.

## What the project is (and isn't)
- **Local only.** It builds a web page and a command line on your own machine. It doesn't post to chat apps,
  run a server, or send data anywhere.
- **Rule-based.** Events are sorted by keywords and patterns from `config.yaml`. No AI or other paid services at runtime.
- **Configurable, not hard-coded.** Interests, sources and regions belong in the user's config, not in code.
  "No interests" means "keep everything", so don't add built-in filtering that users can't turn off,
  apart from the safety filters (region, online-only, excluded words, cancelled, duplicates).

## Adding a new event source
This is the most likely contribution and the one that can do real harm, so please follow every step.

1. **Check that you're allowed.** Read the site's `robots.txt` (`nymetro_eventlocator robots <url>` shows the verdict)
   and its terms of service. If either forbids automated access, the site can't be used, even if it would be easy.
2. **Use the most official access the site offers**, in this order:
   an **iCal feed** → an **official API** → **plain HTML** (e.g. JSON-LD event data) → a **headless browser**.
   - Many sites already have an iCal feed. If so, don't write a source: use `nymetro_eventlocator add <link>`.
   - Sites that only offer an **RSS feed** aren't built as sources either; follow them in an RSS reader.
   - A headless browser (Playwright) is a last resort. Open an issue to discuss it first; the source must also
     explain why in `playwright_justification`.
3. **Never get around protection.** A 401/403/429, a CAPTCHA, a Cloudflare challenge or a login wall means **no**.
   No rotating user agents, proxies, fake browsers or disabled robots checks.
4. **Fetch only through the polite gateway.** Your source gets a `PoliteClient`; use nothing else.
   It enforces robots.txt on every URL and redirect, and at least 5 seconds between requests to a site.
   These limits can't be configured away, and `tests/test_polite.py` fails if another module imports an HTTP library.
5. **Write the source** as `nymetro_eventlocator/sources/<name>.py`:
   ```python
   @register_source("mysite")
   class MySite(Source):
       access = "feed"            # manual | feed | official_api | html | playwright
       tos_note = "robots.txt allows /events; terms permit personal use (checked 2026-10-01)"

       def fetch(self, client: PoliteClient) -> SourceResult: ...
   ```
   Return `Event`s with timezone-aware times. If only part of the source could be fetched, list that part in
   `SourceResult.incomplete`, so stored events from it aren't removed as gone. `sources/confstech.py` is a short example.
6. **Test with synthetic data.** Put fixtures in `tests/fixtures/<name>/`. They must be **made up**: no saved pages
   or API responses containing real people's names, profiles or photos. Serve them with `pytest-httpserver`.
7. **Record the audit** in `docs/sources-compliance.md`: robots.txt, terms, the access used, the verdict and the date.
   Also record sources you checked and **rejected**, with the reason, so nobody spends time on them again.
8. **Keep only what's needed, and credit the provider.** Store future events only (past ones are purged), follow
   the provider's rules on caching and attribution, and add the source to `NOTICE.md`.

## Secrets
- Never commit keys, tokens or webhook URLs. `config.yaml` refers to them as `${NAME}`; values go in the OS secret
  store via `nymetro_eventlocator secrets set NAME`.
- `tests/test_no_secrets.py` fails if something secret-shaped is in a file git would commit. For an extra check on
  every commit: `pip install pre-commit && pre-commit install` (runs gitleaks and ruff).
- Test data uses obviously fake values (e.g. `example-key`).

## Reporting bugs and security issues
- **Bugs:** open an issue with the command you ran, what you expected, what happened, your OS and Python version,
  and the output (check it for keys and personal data first).
- **Security problems** (e.g. a way to leak secrets or inject script into the page): don't open a public issue.
  Use a private security advisory, as described in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the project's [MIT license](LICENSE).
