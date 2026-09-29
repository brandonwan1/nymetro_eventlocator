# Security

## Secrets
- **Never in files you share.**
  - `config.yaml` only *refers* to secrets (`${TICKETMASTER_API_KEY}`).
  - Values live in the OS secret store: Windows Credential Manager, macOS Keychain, Linux Secret Service, or systemd-creds encrypted files bound to the machine and user.
  - The plaintext `.env` fallback is for development and Docker only, and `nymetro_eventlocator secrets list` labels it as plaintext.
- **Never in logs.**
  - The HTTP gateway masks API keys that appear in URLs, and doesn't cache responses for those requests.
  - The `httpx` library's request logging is kept silent, because it prints full URLs.
- **Never in git.**
  - `.gitignore` excludes `config.yaml`, `overrides.yaml`, `.env*`, `*.cred`, `/data/` and `/output/`.
  - `tests/test_no_secrets.py` fails if anything shaped like a webhook, bot token, API key, bearer token or private key is in a file git would commit.
  - CI runs gitleaks over the full git history on every push and pull request.
  - Optionally, enable the same scan locally before each commit: `pip install pre-commit && pre-commit install`.
- **If a key leaks:** revoke and regenerate it at the provider, then `nymetro_eventlocator secrets set NAME` with the new value.

## Dependencies and CI
- Every dependency is pinned to an exact version with hashes (`requirements*.lock`), so an install can't silently
  pick up a new or tampered release.
- CI checks the pinned versions for known vulnerabilities (`pip-audit`) on every push and weekly.
- GitHub Actions are pinned to full commit SHAs, and CI tools (uv, pip-audit, gitleaks) to exact versions;
  the gitleaks download is checksum-verified. Workflows get read-only repository access.

## The web page
- Event text comes from third parties, so the page inserts it with `textContent` (never as HTML), embeds data with `<`/`>`/`&` escaped, and only links `http(s)` URLs.
- It loads MapLibre from jsDelivr and map tiles from OpenFreeMap, and only when the Map tab is opened.

## Reporting a problem
Please open a private security advisory on the repository, rather than a public issue.
