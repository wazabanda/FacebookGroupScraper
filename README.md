# GroupScraper

Scrapes posts, reaction counts and poll options from a Facebook group using Selenium, and writes them to JSON.

Facebook removed its Groups API in April 2024, so this drives a real Chrome browser and reads the page.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Google Chrome.

```bash
uv sync
```

Selenium downloads a matching chromedriver automatically. If you get `This version of ChromeDriver only supports Chrome version N`, an old chromedriver on your `PATH` is taking priority. Remove it (e.g. `/usr/local/bin/chromedriver`).

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Default | Description |
|---|---|---|
| `FB_EMAIL`, `FB_PASSWORD` | | Facebook login. Use a secondary account. |
| `SCROLL_DELAY` | `3,7` | Random wait in seconds between scrolls (`min,max`) |
| `BATCH_SIZE` | `10` | Take a longer break after this many posts |
| `BATCH_PAUSE` | `20,45` | Length of that break in seconds (`min,max`) |
| `FB_COOKIES` | `cookies.json` | Session cookies file. Exported by `--login`, used to log in on a server. |
| `HEADLESS` | `0` | `1` = always run without a window |
| `OUT` | `posts.json` | Output file. `strftime` codes allowed, e.g. `data/posts-%Y%m%d-%H%M.json` |

## Usage

Scrape a group:

```bash
uv run scrape.py https://www.facebook.com/groups/1421088291504611/ --posts 50
```

| Option | Default | Description |
|---|---|---|
| `--posts N` | 20 | Number of posts to collect |
| `--out FILE` | `OUT` | Output file (`strftime` codes allowed) |
| `--headless` | `HEADLESS` | Run Chrome without a window |
| `--login` | | Log in and export session cookies to `FB_COOKIES`, then exit |

### Logging in

If there's no saved session, the script tries the `FB_COOKIES` file, then `FB_EMAIL`/`FB_PASSWORD`. The session is saved to `./fb_profile`. Later runs reuse that session and don't log in again.

If Facebook asks for 2FA or "Is this you?" verification, the first run must have a visible window (no `--headless`) so you can complete it. After that, headless and scheduled runs work. You can also run `uv run scrape.py --login` to log in entirely by hand.

Without credentials, public groups show only a few posts before Facebook's login wall stops the feed.

## Running on a server

Don't log in from the server. Facebook challenges password logins from data-center IPs, and a headless run can't answer the challenge. Log in on your own computer and copy the session cookies to the server instead.

1. **On your computer**, export the cookies:
   ```bash
   uv run scrape.py --login --headless
   ```
   This reuses your saved session, or logs in with `FB_EMAIL`/`FB_PASSWORD`, then writes `cookies.json` and exits. If Facebook asks for verification, run it without `--headless` and complete the check in the window.
2. **Copy it to the server:**
   ```bash
   scp cookies.json server:GroupScraper/
   ```
3. **On the server** (Google Chrome and uv installed, `uv sync` done), `.env` needs only:
   ```
   FB_COOKIES=cookies.json
   HEADLESS=1
   ```
   Leave `FB_EMAIL`/`FB_PASSWORD` out so a failed session never turns into a password login from the server.
4. **Schedule a few runs a day** with cron:
   ```
   0 8,14,20 * * * cd ~/GroupScraper && ~/.local/bin/uv run scrape.py <group_url> --posts 30 --out posts-$(date +\%F-\%H).json >> scrape.log 2>&1
   ```

Every run writes the refreshed cookies back to the file, so the session usually lasts months. When the log says `cookies ... are expired or invalid`, repeat steps 1–2.

The script adds the Chrome options needed in Docker or as root (`--no-sandbox`, `--disable-dev-shm-usage`) automatically.

## Docker

The image bundles Chromium and runs headless. The session, cookies and output go in a `/data` volume.

```bash
docker build -t groupscraper .
mkdir -p data && cp cookies.json data/      # cookies exported with `--login` on your computer

docker run --rm --shm-size=1g -v "$PWD/data:/data" groupscraper \
  https://www.facebook.com/groups/1114412480981108 --posts 30
```

Results land in `./data/posts-YYYYMMDD-HHMM.json`, one file per run. Override with `--out /data/<name>.json`, or set `OUT` in `.env`.

### Docker Compose

Set `GROUP_URL` (and optionally `POSTS`) in `.env`, then:

```bash
docker compose build
docker compose run --rm scraper                                   # scrapes GROUP_URL
docker compose run --rm scraper <group_url> --posts 50            # or any group
```

Compose reads `.env` for credentials and delays, mounts `./data`, and makes sure the Docker paths (`/data/...`) win over any `FB_COOKIES`/`OUT` set in `.env`. For cron: `cd ~/GroupScraper && docker compose run --rm scraper >> scrape.log 2>&1`.

Don't run `docker compose config` without `--no-interpolate`: it prints `.env` values, including your password.

### Notes

- With plain `docker run`, an `--env-file .env` that sets `FB_COOKIES`, `FB_PROFILE` or `OUT` overrides the `/data` paths, so remove those from it. Compose handles this for you. Add `--env-file .env` to pass delays or `FB_EMAIL`/`FB_PASSWORD` as a fallback login.
- `data/fb_profile/` keeps the browser session between runs, so don't delete `data/`.
- For cron, put the `docker run ...` line in the schedule from the section above.

## Output

`posts.json` is a list of posts:

```json
{
  "author": "Kali Siege",
  "text": "What tools does ZORIN has...",
  "posted_at": "2026-10-14T23:50:00+02:00",
  "posted_at_text": "Wednesday, October 14, 2026 at 11:50 PM",
  "url": "https://www.facebook.com/groups/1421088291504611/posts/5095958447350892/",
  "reactions_total": "4",
  "reaction_types": {"Like": "3", "Love": "1"},
  "poll": [
    {"option": "Yes", "share": "50%", "you_voted": true, "votes": "1",
     "voters": [{"name": "Jane Doe", "profile": "https://www.facebook.com/jane.doe"}]},
    {"option": "No", "share": "50%", "you_voted": false, "votes": "1",
     "voters": [{"name": "Anonymous participant", "profile": null}]}
  ],
  "raw": "full visible text of the post"
}
```

- `reaction_types` covers only the top 3 reaction types, which is all Facebook shows under a post.
- Counts are strings as Facebook displays them (e.g. `"1.2K"`).
- `poll` is empty for posts without a poll. `share` is the percentage Facebook shows; `you_voted` is whether the logged-in account picked that option. `votes` and `voters` come from opening each option's voter list, which needs a login; `profile` is `null` for anonymous voters.
- `posted_at` comes from the tooltip shown when hovering over the post's time (the visible time text is scrambled by Facebook). It's in the browser's timezone: your computer's, or UTC in Docker unless you set `TZ` (e.g. `TZ: Africa/Lusaka` under `environment` in `compose.yaml`). `posted_at_text` keeps the original string. Both are `null` if the hover fails.
- The same hover fills in `url`.

## Limitations

- Facebook changes its page markup often. When a field starts returning `null`, the selectors in `EXTRACT_JS` in `scrape.py` need updating.
- Scraping is against Facebook's Terms of Service. Use a secondary account, keep runs small, and don't run several at once. Accounts can be checkpointed or banned.
- `.env` holds your password in plain text, and `fb_profile/` and `cookies.json` give full access to the account. Don't commit or share any of them (all are in `.gitignore`).
- Fresh logins are the riskiest part for bans. Let the saved session do the work instead of deleting `fb_profile/`.
- Collecting members' data from a group may fall under GDPR or local privacy law.
