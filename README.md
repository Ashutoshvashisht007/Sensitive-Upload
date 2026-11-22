# Sensitive Upload Guard

A small desktop app for macOS that stops prompts and files containing `WALLED_SENSITIVE` from reaching **ChatGPT** or **Perplexity** until you say so. It shows a **native macOS dialog** before anything leaves the Mac.

There's **no browser extension**. The app sits in the menu bar, and you keep using your normal Chrome with your normal profile.

- **Demo video:** _link added after upload_
- **How it works:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [design doc (PDF)](docs/design-doc.pdf)

<p align="center"><img src="docs/img/dialog-file.png" width="260" alt="Native dialog for a scanned PDF with the marker on page 2"></p>

## What it does

- **Prompts.** When you send a prompt, the app holds the request on its way out. If the text contains the marker, a dialog shows an excerpt and asks. Clean prompts go straight through.
- **Files.** Uploads are held while the app reads the file's bytes:
  - text files, including UTF-16;
  - PDFs, with scanned pages read by OCR;
  - images, read by OCR, plus their metadata;
  - Office documents and zip files.

  If the marker is there, the dialog names the file and says where the marker was found. ChatGPT's newer upload flow sends a file's name only after its bytes, so the app finds the name by matching the bytes to the file on your Mac (Spotlight, then a hash). Only the name is used, and nothing leaves the Mac.
- **Continue** forwards the request exactly as the browser sent it. **Cancel** answers it on the Mac, so nothing reaches the site. ChatGPT shows "Blocked by Sensitive Upload Guard. Nothing was sent." with a Retry button; Perplexity shows its own "Something went wrong while generating this response." Either way you can edit and try again.
- **Analytics.** Both sites copy what you type into their analytics and telemetry. Those requests are blocked silently when they carry the marker, unless you chose Continue for that text.
- **AI responses are recoloured** on both sites, in light and dark themes.
- **Nothing else is touched.** Only ChatGPT, Perplexity and their upload hosts are routed through the app; every other site goes direct.
- **It fails closed.** If the app isn't running, ChatGPT and Perplexity won't load until it's back, while every other site keeps working.

```
Your Chrome (normal profile, nothing installed in it)
   │  proxy auto-config: only chatgpt.com, perplexity.ai and their upload hosts → 127.0.0.1:8899
   ▼
Sensitive Upload Guard (menu-bar app)
   holds each outgoing request ─► inspects text / file bytes (PDF, OCR via macOS Vision)
   marker? ─► native dialog ─► Continue: forward unchanged · Cancel: answer 403 locally
   pages on the way back ─► add a <style> that recolours AI answers
   ▼
chatgpt.com / perplexity.ai          (all other sites: DIRECT, never touch the app)
```

## Requirements

- macOS; built and tested on macOS 27 on Apple Silicon.
- Google Chrome. Edge, Brave, Safari and other apps that use the macOS proxy settings and certificate store are covered the same way.
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. I used Python 3.13 from python.org.
- Optional: `tesseract` (`brew install tesseract`), used as a fallback OCR engine.

## Setup

```sh
git clone https://github.com/Ashutoshvashisht007/Sensitive-Upload.git
cd Sensitive-Upload
uv sync
uv run upload-guard install
```

Then **quit and reopen Chrome**, so it picks up the new proxy settings.

`install` does three things. macOS asks for your password for the first two.

1. **Creates a local certificate authority** for this Mac and trusts it in your login keychain. The app uses it to read traffic to the AI sites only. The private key stays in `~/.upload-guard/ca/` and never leaves the Mac.
2. **Sets a proxy auto-config** on every network service. It sends only the AI hosts to `127.0.0.1:8899` and everything else direct. Your previous settings are saved.
3. **Adds a login item**, so the app starts at login and restarts if it crashes.

`uv run upload-guard uninstall` undoes all three and restores your previous proxy settings.

To try it without installing anything system-wide, run `uv run upload-guard run` and point a browser at the PAC yourself:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --user-data-dir=/tmp/ug-try \
  --proxy-pac-url="$(uv run python -c 'from upload_guard.proxy.pac import pac_data_url; print(pac_data_url(8899))')"
```

That Chrome profile doesn't trust the local CA unless you run `install`. Use it only to see the routing.

## Usage

Use ChatGPT and Perplexity as usual. The shield in the menu bar shows that the app is running, plus the last few decisions. The `demo/` folder has files to try:

| File | What happens |
|---|---|
| `notes.txt` | Marker on line 5 → dialog |
| `contract.pdf` | Marker on page 2 → dialog |
| `scanned.pdf` | Image-only PDF, marker on scanned page 2 → dialog (via OCR) |
| `whiteboard.png` | Marker written on a whiteboard photo → dialog (via OCR) |
| `clean.pdf`, `clean.png` | No dialog |

For a prompt, send anything containing `WALLED_SENSITIVE`.

### Command line

| Command | |
|---|---|
| `uv run upload-guard install` / `uninstall` | Set up or remove everything above. `install` also takes `--no-trust`, `--no-proxy` and `--no-agent` |
| `uv run upload-guard run [--headless]` | Run the app in the foreground. `install` normally starts it for you |
| `uv run upload-guard status` | Running? CA trusted? PAC set on each network service? Plus the last decisions |
| `uv run upload-guard scan FILE…` | Check files exactly as uploads are checked. Exit code 0 means clean, 1 flagged or uninspectable, 2 unreadable |
| `uv run upload-guard dialog-test` | Show a sample dialog |
| `uv run upload-guard log-requests on\|off` | Troubleshooting: log method, host, path and size of AI-site requests. Never content |

### Settings and files

- **`~/.upload-guard/config.json`** (optional):
  - `proxy_port`: default 8899;
  - `max_inspect_mb`: default 25;
  - `ocr_max_pages`: default 15;
  - `fail_mode`: `"closed"` by default, which asks about files that can't be inspected;
  - `recolor`, `color_light`, `color_dark`;
  - `log_requests`.
- **`~/.upload-guard/`** (mode 0700) holds:
  - the CA;
  - `app.log`;
  - `audit.jsonl` (0600): one line per flagged prompt or file, with time, site, file name or prompt length, where the marker was, and the decision. **Never the content.**

## Tests

```sh
uv run pytest      # 169 tests
```

| Suite | Tests | What it covers |
|---|---|---|
| End to end (`tests/e2e`) | 21 | Chromium uses only the app's PAC and goes through the real guard to a local stand-in for both sites (`tests/mock_sites`), which records what arrives. Covers clean prompts and files, Cancel and Continue for prompts and for txt / PDF / scanned PDF / PNG, typeahead never leaking the marker, oversized files, recolouring under a nonce CSP, other sites going direct, and the AI sites failing closed when the app is down |
| Add-on | 34 | The guard on mitmproxy flows: dialog wording, upload file names (including ChatGPT's live `upload_reservations` flow, which names the file after the bytes), automatic upload retries, multipart uploads, size limits, telemetry copies of a prompt, recolouring, cache revalidation and pages fetched by a service worker |
| File naming | 4 | Matching an unnamed upload to the file on disk, with Spotlight's results replaced by a fixed list |
| Units | 37 | Site rules, body parsing, CSP patching, PAC routing (evaluated in Node), the decision memory |
| Installer | 8 | The CA, PAC backup and restore, a single admin prompt, the login item. System tools are mocked |
| Detectors | 33 | Every detector on generated fixtures, including OCR and the tesseract fallback |
| Marker rules | 30 | Shared vectors: case, zero-width characters, fullwidth, OCR confusions, near-misses |
| Dialog | 2 | Wording, and the `osascript` fallback can't be injected |

The end-to-end tests are skipped if Playwright's Chromium isn't installed (`uv run playwright install chromium`).

There are also two macOS checks that briefly show real windows:

```sh
uv run python scripts/check_fullscreen_dialog.py   # dialog appears over a full-screen app, same display, with focus
uv run python scripts/check_dialog_closes.py       # dialog really closes after it's answered
```

## Troubleshooting

- **Something isn't working.** Run `uv run upload-guard status`; every line should say `yes`.
- **Perplexity answers aren't teal right after installing.** Reload twice. Perplexity's service worker shows its own cached copy of the page first, and the app colours the fresh copy that replaces it.
- **ChatGPT or Perplexity won't load.** The app isn't running. Click the shield or run `uv run upload-guard run`. If you just installed, quit and reopen Chrome.
- **A certificate warning on the AI sites.** The local CA isn't trusted. Run `install` again; it asks for your password.
- **A site changed and something isn't caught.** Turn on `upload-guard log-requests on`, use the site, and read `~/.upload-guard/requests.log` (method, host, path, size). Endpoints and hosts are in `src/upload_guard/sites.py`. Anything not listed is still checked:
  - any POST/PUT carrying the marker gets the dialog;
  - background requests carrying it are blocked;
  - uploads to a host that isn't routed are flagged in the log.
- **You already use a proxy auto-config,** for example at work. `install` replaces it while the app is installed, and `uninstall` puts it back.

## Limitations

- **macOS only.** The installer, the dialog and OCR use macOS APIs. The core proxy would run elsewhere, but it's untested there.
- **Firefox** uses its own certificate store, so it needs the CA added there too. Apps that pin certificates can't be read, and would fail if routed. The ChatGPT desktop app is worth checking before relying on this.
- **Cancel shows the site's own error, or none.** At the network level the app can stop a request but can't keep text in the site's message box. ChatGPT shows the block message with a Retry button; Perplexity shows its generic "Something went wrong" error. You can edit and resend on both.
- **Endpoints change.** The rules in `sites.py` name the known send and upload endpoints. The general rules catch anything else that carries the marker, but a brand-new upload host has to be added to the routing list.
- **OCR** can miss tiny, stylised or handwritten text. In OCR'd text the match is tolerant, so it can occasionally flag the two words "walled sensitive" in an image. Files that can't be read ask instead of passing. Only the first frame of a GIF or TIFF is read.
- **Large files.** Uploads over 25 MB aren't inspected; the user is asked first.
- **Not a hard boundary.** It guards against mistakes, not a determined user, who could uninstall it or change the proxy settings. A managed deployment would lock those settings with a configuration profile.

## Layout

```
src/upload_guard/
  app.py              menu-bar app: AppKit on the main thread, the guard on a background thread
  proxy/addon.py      the guard: classify, inspect, decide, forward or block; recolouring on the way back
  proxy/server.py     runs mitmproxy with the guard; the local CA
  proxy/pac.py        proxy auto-config (only the AI hosts)
  proxy/bodies.py     JSON, form, multipart and query parsing
  proxy/recolor.py    response-colour CSS and CSP-safe injection
  sites.py            per-site hosts, endpoints and answer selectors
  locate.py           names an upload from the file on disk when the site hasn't said yet
  decisions.py        one dialog per piece of content, on the right thread
  detect/             marker rules, file type sniffing, text / PDF / image / zip detectors, OCR
  dialog/             NSAlert (works over full-screen apps), osascript fallback, test backends
  install.py, cli.py  install / uninstall / status and the upload-guard command
tests/                unit, add-on, installer and end-to-end tests; mock sites; fixtures
scripts/              fixture and icon generators, macOS dialog checks
demo/                 files used in the demo
docs/                 ARCHITECTURE.md, design-doc.pdf
```
