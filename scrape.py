"""Scrape posts, reactions and polls from a Facebook group with Selenium.

    uv run scrape.py <group_url> --posts 30       # writes posts.json, logs in from .env if needed
    uv run scrape.py --login                      # optional: log in by hand (2FA etc.), session saved to ./fb_profile
"""
import argparse
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

HERE = Path(__file__).parent
PROFILE = Path(os.environ.get("FB_PROFILE") or HERE / "fb_profile")


def load_env(path=HERE / ".env"):
    # minimal KEY=VALUE parser, swap for python-dotenv if you need quoting/multiline
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def env_range(name, default):
    lo, hi = os.environ.get(name, default).split(",")
    return float(lo), float(hi)

# Runs in the page: pull everything we need from one feed item in a single round trip.
EXTRACT_JS = r"""
const post = arguments[0];
const q = (s) => post.querySelector(s);
const txt = (el) => (el ? el.innerText.trim() : null);
const link = [...post.querySelectorAll('a[href]')].map(a => a.href.split('?')[0])
  .find(h => /\/(posts|permalink)\/\w+/.test(h)) || null;
// icons carry aria-label="Like: 12 people" (FB shows the top 3 types only)
const reaction_types = {};
for (const e of post.querySelectorAll('[aria-label]')) {
  const m = e.getAttribute('aria-label').match(/^(\w+): ([\d.,]+\s*[KM]?) (?:people|person)$/);
  if (m) reaction_types[m[1]] = m[2];
}
const total = post.innerText.match(/All reactions:\s*([\d.,]+\s*[KM]?)/);
// poll options carry aria-label="Yes, 50% of the votes"; the sibling checkbox is ticked if you voted
const poll = [...post.querySelectorAll('[aria-label$="of the votes"]')].map(o => {
  const m = o.getAttribute('aria-label').match(/^(.*), (.+?) of the votes$/);
  const box = o.parentElement.closest('[role="button"]')?.querySelector('input[type="checkbox"]');
  return { option: m[1], share: m[2], you_voted: box ? box.checked : null };
});
const actions = [...post.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label'))
  .find(a => /^Actions for this post/.test(a));
const is_post = !!link || !!actions;
// normal posts: message block; text-on-background posts: story_message, which repeats the text in an aria-hidden copy
const story = () => {
  const el = q('[data-ad-rendering-role="story_message"]');
  if (!el) return null;
  const c = el.cloneNode(true);
  c.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
  return c.textContent.trim() || null;
};
return {
  author: actions ? actions.replace(/^Actions for this post by /, '') : txt(q('h2, h3, h4')),
  text: txt(q('[data-ad-preview="message"], [data-ad-comet-preview="message"]')) || story(),
  url: link,
  reactions_total: total ? total[1].trim() : null,  // FB hides the total when there's one reaction type
  is_post,
  reaction_types,
  poll,
  // logged-in feed pads posts with hidden "Facebook" lines
  raw: post.innerText.split('\n').filter(l => l.trim() !== 'Facebook').join('\n').trim(),
};
"""


def make_driver(headless):
    # A run that was killed leaves Singleton* lock links naming its host; in a fresh container (new hostname)
    # Chrome then thinks another machine owns the profile and exits. ponytail: assumes one run at a time.
    for f in PROFILE.glob("Singleton*"):
        f.unlink(missing_ok=True)
    opts = webdriver.ChromeOptions()
    if os.environ.get("CHROME_BIN"):
        opts.binary_location = os.environ["CHROME_BIN"]  # e.g. Chromium in Docker
    opts.add_argument(f"--user-data-dir={PROFILE.resolve()}")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-dev-shm-usage")  # Docker's /dev/shm is too small for Chrome
    if os.geteuid() == 0:
        opts.add_argument("--no-sandbox")  # Chrome refuses to start as root otherwise
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1366,900")
    return webdriver.Chrome(options=opts)


def cookie_file():
    return Path(os.environ.get("FB_COOKIES") or HERE / "cookies.json")


def save_cookies(d):
    if (p := cookie_file()) and logged_in(d):
        p.write_text(json.dumps(d.get_cookies(), indent=1))
        p.chmod(0o600)


def load_cookies(d):
    """Log in from a cookies file exported on another machine (see README: Running on a server)."""
    p = cookie_file()
    if not (p and p.exists()):
        return
    for c in json.loads(p.read_text()):
        if c.get("sameSite") not in ("Strict", "Lax", "None"):
            c.pop("sameSite", None)
        try:
            d.add_cookie(c)
        except Exception:
            pass
    d.refresh()
    nap(2, 4)
    print("logged in from cookies" if logged_in(d) else f"cookies in {p} are expired or invalid")


def nap(a, b):
    time.sleep(random.uniform(a, b))


def logged_in(d):
    return d.get_cookie("c_user") is not None


def human_type(el, text):
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(0.05, 0.2))


def auto_login(d, headless):
    email, password = os.environ.get("FB_EMAIL"), os.environ.get("FB_PASSWORD")
    if not (email and password):
        print("not logged in and FB_EMAIL/FB_PASSWORD not set in .env - continuing logged out")
        return
    d.get("https://www.facebook.com/login")
    nap(2, 4)
    human_type(d.find_element(By.NAME, "email"), email)
    nap(0.5, 1.5)
    human_type(d.find_element(By.NAME, "pass"), password)
    nap(0.5, 1.5)
    d.find_element(By.NAME, "pass").send_keys(Keys.ENTER)
    for _ in range(20):
        if logged_in(d):
            print("logged in")
            return
        time.sleep(1)
    # 2FA / "is this you?" checkpoint: needs a human once, then the saved session is reused
    if headless:
        raise SystemExit(f"login did not complete (at {d.current_url}). Run once without --headless or use --login.")
    input("Finish verification in the browser window, then press Enter here...")
    if not logged_in(d):
        raise SystemExit("still not logged in")


def dismiss_login_wall(d):
    for el in d.find_elements(By.CSS_SELECTOR, '[aria-label="Close"][role="button"]'):
        try:
            el.click()
        except Exception:
            pass
    d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)


# Runs in the open voter dialog: scroll its list to load everyone, then read count + voters.
VOTERS_JS = r"""
const done = arguments[arguments.length - 1];
const dl = [...document.querySelectorAll('[role="dialog"]')].pop();
if (!dl) return done(null);
const box = [...dl.querySelectorAll('div')].find(e => e.scrollHeight > e.clientHeight + 10);
// names come from the text (anonymous voters have no profile link), profiles from links where present
const read = () => {
  const links = {};
  for (const a of dl.querySelectorAll('a[aria-label^="Profile picture of "]'))
    links[a.getAttribute('aria-label').replace('Profile picture of ', '')] = a.href.split('?')[0];
  const lines = dl.innerText.split('\n').map(l => l.trim()).filter(Boolean);
  const i = lines.findIndex(l => /\bVotes?$/i.test(l));
  return lines.slice(i + 1).map(name => ({ name, profile: links[name] || null }));
};
let last = -1, tries = 0;
const step = () => {
  const n = read().length;
  if (!box || n === last || tries++ > 50) {  // ponytail: stops after ~50 scrolls, raise for huge polls
    const m = dl.innerText.match(/([\d.,]+\s*[KM]?)\s+Votes?/i);
    return done({ votes: m ? m[1].trim() : null, voters: read() });
  }
  last = n;
  box.scrollTop = box.scrollHeight;
  setTimeout(step, 1500 + Math.random() * 1500);
};
setTimeout(step, 1500);
"""


# The time link: points at the post, or at the group root until hovered. Its text is scrambled on purpose.
TIME_LINK_JS = r"""
const as = [...arguments[0].querySelectorAll('a[role="link"]')];
return as.find(a => /\/(posts|permalink)\//.test(a.href))
  || as.find(a => /\/groups\/[^/]+\/?$/.test(new URL(a.href).pathname) && a.innerText.trim());
"""


def parse_fb_time(s):
    """'Wednesday, October 14, 2026 at 11:50 PM' -> ISO 8601 in the browser's timezone."""
    try:
        t = datetime.strptime(s.replace(" ", " ").strip(), "%A, %B %d, %Y at %I:%M %p")
        return t.astimezone().isoformat()
    except ValueError:
        return None


def post_time(d, post):
    """Hover the time link: the tooltip has the full date and the link turns into the post URL."""
    try:
        a = d.execute_script(TIME_LINK_JS, post)
        if not a:
            return None, None
        d.execute_script("arguments[0].scrollIntoView({block: 'center'})", a)
        ActionChains(d).move_to_element(a).perform()
        nap(1.2, 2)
        tips = [t.text for t in d.find_elements(By.CSS_SELECTOR, '[role="tooltip"]') if t.text.strip()]
        ActionChains(d).move_by_offset(0, 150).perform()  # move off so the tooltip closes
        href = (a.get_attribute("href") or "").split("?")[0]
        return (tips[-1] if tips else None), (href if "/posts/" in href or "/permalink/" in href else None)
    except Exception:
        return None, None


# Reads the reactions dialog: one tab per type, aria-label="Show 3 people who reacted with Love".
# Types that don't fit sit under a "More" tab whose menu items use the same label.
REACTIONS_JS = r"""
const re = /^Show ([\d.,]+\s*[KM]?) (?:people|person) who reacted with (\w+)$/;
const out = {};
for (const e of document.querySelectorAll('[aria-label*="who reacted with"]')) {
  const m = e.getAttribute('aria-label').match(re);
  if (m) out[m[2]] = m[1];
}
// "More" menu rows are icon + number; take a name from any label/alt, else keep the count as other_N
let n = 0;
for (const it of document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]')) {
  const count = (it.innerText.match(/[\d.,]+\s*[KM]?/) || [])[0];
  if (!count) continue;
  const label = [it, ...it.querySelectorAll('[aria-label], img[alt]')]
    .map(e => e.getAttribute('aria-label') || e.getAttribute('alt') || '').join(' ');
  const m = label.match(re) || label.match(/\b(Like|Love|Care|Haha|Wow|Sad|Angry)\b/i);
  const name = m ? (m.length > 2 ? m[2] : m[1]) : `other_${++n}`;
  if (!(name in out)) out[name] = count.trim();
}
return out;
"""


def reaction_counts(d, post):
    """Open the reactions dialog from the first reaction icon, read every type's count, close it."""
    icons = post.find_elements(By.CSS_SELECTOR, '[aria-label="See who reacted to this"] [role="button"]')
    if not icons:
        return None
    try:
        d.execute_script("arguments[0].scrollIntoView({block: 'center'})", icons[0])
        nap(0.5, 1)
        try:
            icons[0].click()
        except ElementClickInterceptedException:
            d.execute_script("arguments[0].click()", icons[0])
        nap(2, 3.5)
        dialog = next((dl for dl in d.find_elements(By.CSS_SELECTOR, '[role="dialog"]')
                       if dl.find_elements(By.CSS_SELECTOR, '[aria-label*="who reacted with"]')), None)
        if not dialog:
            return None
        more = [t for t in dialog.find_elements(By.CSS_SELECTOR, '[role="tab"], [role="button"], [aria-haspopup]')
                if t.text.strip().startswith("More")]
        if more:  # overflow types (e.g. Care, Wow, Sad) live in a dropdown menu
            try:
                more[0].click()
            except ElementClickInterceptedException:
                d.execute_script("arguments[0].click()", more[0])
            nap(1, 2)
        counts = d.execute_script(REACTIONS_JS)
        close = dialog.find_elements(By.CSS_SELECTOR, '[aria-label="Close"]')
        d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)  # closes the More menu if open
        if close and close[0].is_displayed():
            close[0].click()
        nap(1, 2)
        return counts or None
    except Exception as e:
        print(f"  reactions dialog failed: {e.__class__.__name__}")
        return None


def poll_voters(d, post, poll):
    """Click each option's percentage arrow, read the voter dialog, close it."""
    d.set_script_timeout(120)
    for opt, el in zip(poll, post.find_elements(By.CSS_SELECTOR, '[aria-label$="of the votes"]')):
        try:
            d.execute_script("arguments[0].scrollIntoView({block: 'center'})", el)
            nap(0.5, 1)
            try:
                el.click()  # the arrow opens the dialog; it does not change the vote
            except ElementClickInterceptedException:
                d.execute_script("arguments[0].click()", el)
            nap(2, 3)
            opt.update(d.execute_async_script(VOTERS_JS) or {})
            close = d.find_elements(By.CSS_SELECTOR, '[role="dialog"] [aria-label="Close"]')
            close[-1].click() if close else d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            nap(1, 2.5)
        except Exception as e:
            print(f"  voters failed for {opt['option']!r}: {e.__class__.__name__}")


def scrape(url, limit, headless):
    scroll_delay = env_range("SCROLL_DELAY", "3,7")
    batch_pause = env_range("BATCH_PAUSE", "20,45")
    batch_size = int(os.environ.get("BATCH_SIZE", "10"))
    d = make_driver(headless)
    try:
        d.get("https://www.facebook.com/")
        nap(2, 4)
        if not logged_in(d):
            load_cookies(d)
        if not logged_in(d):
            auto_login(d, headless)
        save_cookies(d)  # keep the file fresh: FB rotates session cookies
        d.get(url)
        nap(4, 8)
        posts, seen, stale = [], set(), 0
        while len(posts) < limit and stale < 5:
            dismiss_login_wall(d)
            items = d.find_elements(By.CSS_SELECTOR, '[role="feed"] > div, div[aria-posinset]')
            before = len(posts)
            for it in items:
                try:
                    data = d.execute_script(EXTRACT_JS, it)
                except StaleElementReferenceException:
                    break  # feed re-rendered (e.g. after a poll dialog): re-query it
                key = data["url"] or (data["raw"] or "")[:200]
                if not data.pop("is_post") or key in seen:
                    continue
                seen.add(key)
                if data["reactions_total"] is None and all(v.isdigit() for v in data["reaction_types"].values()):
                    data["reactions_total"] = str(sum(map(int, data["reaction_types"].values()))) if data["reaction_types"] else "0"
                tip, href = post_time(d, it)
                data["posted_at"], data["posted_at_text"] = parse_fb_time(tip) if tip else None, tip
                data["url"] = data["url"] or href
                if data["url"]:
                    if data["url"] in seen and data["url"] != key:
                        continue  # same post seen earlier under its text key (link only appears after hover)
                    seen.add(data["url"])
                if data["reaction_types"]:
                    full = reaction_counts(d, it)
                    if full:
                        data["reaction_types"] = full
                        if all(v.isdigit() for v in full.values()):
                            data["reactions_total"] = str(sum(map(int, full.values())))
                if data["poll"]:
                    poll_voters(d, it, data["poll"])
                posts.append(data)
                print(f"[{len(posts)}] {(data['author'] or '?')[:30]} | reactions={data['reactions_total']} {data['reaction_types']} | poll={[(o['option'], o.get('votes')) for o in data['poll']] or '-'}")
                if len(posts) >= limit:
                    break
                if len(posts) % batch_size == 0:
                    t = random.uniform(*batch_pause)
                    print(f"  pausing {t:.0f}s after {len(posts)} posts")
                    time.sleep(t)
            stale = stale + 1 if len(posts) == before else 0
            # scroll a random distance so it doesn't look scripted
            d.execute_script("window.scrollBy(0, window.innerHeight * arguments[0])", random.uniform(0.8, 2.2))
            nap(*scroll_delay)
        save_cookies(d)
        return posts
    finally:
        d.quit()


def login(headless):
    """Get a session (saved profile -> .env credentials -> by hand) and export it to the cookies file."""
    d = make_driver(headless)
    try:
        d.get("https://www.facebook.com/")
        nap(2, 4)
        if not logged_in(d) and os.environ.get("FB_EMAIL"):
            auto_login(d, headless)
        if not logged_in(d):
            if headless:
                raise SystemExit("not logged in: set FB_EMAIL/FB_PASSWORD in .env or run without --headless")
            input("Log in in the browser window, then press Enter here...")
        if not logged_in(d):
            raise SystemExit("still not logged in")
        save_cookies(d)
        print(f"session cookies saved to {cookie_file()} - copy this file to the server")
    finally:
        d.quit()


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--posts", type=int, default=20)
    ap.add_argument("--out", default=os.environ.get("OUT", "posts.json"),
                    help="output file; strftime codes allowed, e.g. posts-%%Y%%m%%d-%%H%%M.json")
    ap.add_argument("--headless", action="store_true", default=os.environ.get("HEADLESS") == "1")
    ap.add_argument("--login", action="store_true", help="log in and export session cookies (for a server), then exit")
    a = ap.parse_args()
    if a.login:
        login(a.headless)
    elif a.url:
        out = Path(time.strftime(a.out))
        out.parent.mkdir(parents=True, exist_ok=True)
        res = scrape(a.url, a.posts, a.headless)
        out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"saved {len(res)} posts -> {out}")
    else:
        ap.error("give a group url or --login")
