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

## Usage

Scrape a group:

```bash
uv run scrape.py https://www.facebook.com/groups/1421088291504611/ --posts 50
```

| Option | Default | Description |
|---|---|---|
| `--posts N` | 20 | Number of posts to collect |
| `--out FILE` | `posts.json` | Output file |
| `--headless` | off | Run Chrome without a window |
| `--login` | | Open Chrome to log in by hand and save the session |

### Logging in

If there's no saved session, the script logs in with `FB_EMAIL`/`FB_PASSWORD`, and the session is saved to `./fb_profile`. Later runs reuse that session and don't log in again.

If Facebook asks for 2FA or "Is this you?" verification, the first run must have a visible window (no `--headless`) so you can complete it. After that, headless and scheduled runs work. You can also run `uv run scrape.py --login` to log in entirely by hand.

Without credentials, public groups show only a few posts before Facebook's login wall stops the feed.

## Output

`posts.json` is a list of posts:

```json
{
  "author": "Kali Siege",
  "text": "What tools does ZORIN has...",
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
- `url` is often `null` when logged in, because Facebook only fills in post links when the mouse hovers over them.

## Limitations

- Facebook changes its page markup often. When a field starts returning `null`, the selectors in `EXTRACT_JS` in `scrape.py` need updating.
- Scraping is against Facebook's Terms of Service. Use a secondary account, keep runs small, and don't run several at once. Accounts can be checkpointed or banned.
- `.env` holds your password in plain text and `fb_profile/` holds your session cookies. Don't commit or share either (both are in `.gitignore`).
- Fresh logins are the riskiest part for bans. Let the saved session do the work instead of deleting `fb_profile/`.
- Collecting members' data from a group may fall under GDPR or local privacy law.
