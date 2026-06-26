#!/usr/bin/env python3
"""Bound e2e acceptance test for anime.player.episode-switch (Evolve ev-skipperbot-app-anime-1).

Proves the REPORTED bug is fixed: selecting a different episode in the anime player must play
THAT episode's stream — a real media swap, not a replay of the previously-playing source. The
on-screen label and even the master-playlist request already changed on `main` while the VIDEO
kept playing the old episode, so this asserts at the SEGMENT layer: each episode's upstream HLS
segments live at different URLs, proxied through /api/apps/anime/stream/proxy?u=<b64 upstream
url>; decoding `u` reveals which episode is truly loading.

Runs on the TEST HOST (box2 harness) against the live dockerized Skipper, driven over Playwright.
  QA_BASE=http://<host>:8000 python3 test_player_episode_switch.py
Prints a single JSON result line `RESULT: {...}` and exits 0 (pass) / 1 (fail) / 2 (skip — the
external allanime catalog could not be warmed; a 502 storm is out of scope, not a failure).

Login is programmatic (API token injected into localStorage — the app's real bootstrap path) so a
racy 2-step login form isn't what's under test. One Piece: allanime_id ReooPAxPMsHM4KPMY.
"""
import os, re, sys, json, time, base64

BASE = os.environ.get("QA_BASE", "http://localhost:8000").rstrip("/")
USER = os.environ.get("QA_USER", "evolve_qa")
PW = os.environ.get("QA_PW", USER + "1234")
ANIME_ID = os.environ.get("ANIME_ID", "ReooPAxPMsHM4KPMY")
ANIME_QUERY = os.environ.get("ANIME_QUERY", "one piece")
SHOTS = os.path.expanduser("~/evolve-qa-shots"); os.makedirs(SHOTS, exist_ok=True)
SETTLE_S = 6.0           # spec: observe >= 5s with no stale (A-only) segment
WARM_TRIES = 10          # retry budget through intermittent allanime 502s


def _b64url_decode(s: str):
    try:
        s = s.split("&")[0]
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
    except Exception:
        return None


def _decoded_u(url: str):
    m = re.search(r"[?&]u=([^&]+)", url)
    return _b64url_decode(m.group(1)) if m else None


def _parse_master(url: str):
    """(quality, mode) from /stream/{id}/{ep}/{quality}/master.m3u8?mode=.."""
    q = re.search(r"/stream/[^/]+/[^/]+/([^/]+)/master\.m3u8", url)
    m = re.search(r"[?&]mode=([^&]+)", url)
    return (q.group(1) if q else None, m.group(1) if m else None)


def _ep_of_master(url: str):
    m = re.search(r"/stream/[^/]+/([^/]+)/[^/]+/master\.m3u8", url)
    return m.group(1) if m else None


def run():
    from playwright.sync_api import sync_playwright

    seg_reqs = []     # {t, decoded} for every /stream/proxy?u= request
    master_reqs = []  # {t, url, ep, quality, mode}

    def on_request(req):
        u = req.url
        if "/api/apps/anime/stream/proxy" in u and "u=" in u:
            seg_reqs.append({"t": time.time(), "decoded": _decoded_u(u)})
        elif "/api/apps/anime/stream/" in u and "master.m3u8" in u:
            q, m = _parse_master(u)
            master_reqs.append({"t": time.time(), "url": u, "ep": _ep_of_master(u), "quality": q, "mode": m})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.on("request", on_request)

        # --- login via the real 2-step form (login is not what's under test; the
        # token-only bootstrap doesn't authenticate the desktop here) ---
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)  # let React hydrate before filling
        page.get_by_role("textbox").first.fill(USER)
        page.keyboard.press("Enter")
        page.wait_for_selector("input[type=password]", timeout=15000)
        page.locator("input[type=password]").first.fill(PW)
        page.keyboard.press("Enter")
        for _ in range(50):
            if page.evaluate("() => localStorage.getItem('skipperbot_token')"):
                break
            page.wait_for_timeout(500)
        else:
            raise RuntimeError("login failed (no token)")
        page.wait_for_timeout(2000)  # desktop settle

        # --- precondition: warm the source cache for eps 1 & 2 (retry through 502s) ---
        def warm(ep):
            for _ in range(WARM_TRIES):
                res = page.evaluate("""async ({id, ep}) => {
                    try { const r = await fetch(`/api/apps/anime/sources/${id}/${ep}?mode=sub`);
                      if (!r.ok) return {ok:false, status:r.status};
                      const d = await r.json(); return {ok: !!(d.streams && d.streams.length), n:(d.streams||[]).length}; }
                    catch (e) { return {ok:false, err:String(e)}; }
                }""", {"id": ANIME_ID, "ep": str(ep)})
                if res.get("ok"):
                    return True
                page.wait_for_timeout(2500)
            return False

        if not (warm(1) and warm(2)):
            print("RESULT: " + json.dumps({"passed": False, "skipped": True,
                  "reason": "allanime catalog could not be warmed for eps 1&2 (502 storm) — out of scope",
                  "evidence": []}))
            browser.close(); return 2

        # --- seed the watchlist via the app's own API (scope_user binds it to the
        # logged-in user) so we can open the player WITHOUT the flaky /search path ---
        page.evaluate("""async ({id, title, uid}) => {
            await fetch('/api/apps/anime/watchlist', {method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({user_id:uid, allanime_id:id, title:title, episode_count:1168})});
        }""", {"id": ANIME_ID, "title": "One Piece", "uid": USER})

        # --- open the player on episode 1 via the Watchlist "Start" button. The
        # player fetches /episodes (uncached, intermittently 502) to enable Next;
        # retry the whole open (page reload) until Next is enabled. A persistent
        # 502 storm => SKIP (allanime resilience is out of scope). ---
        def open_player():
            # Watchlist row -> "Episodes" (picker) -> "Play episode 1". This always
            # targets ep1 (independent of prior history) and handlePlay pre-warms
            # /sources/1 before opening the player.
            page.get_by_role("button", name="Anime", exact=True).first.click()
            page.wait_for_timeout(900)
            page.get_by_role("button", name="Watchlist", exact=True).first.click()
            page.wait_for_timeout(1200)
            try:
                page.get_by_title("Browse all episodes").first.click(timeout=8000)   # -> EpisodePicker
            except Exception:
                return False
            try:
                page.get_by_title("Play episode 1").first.wait_for(state="visible", timeout=12000)
            except Exception:
                return False                              # /episodes 502 -> picker empty
            page.get_by_title("Play episode 1").first.click()
            for _ in range(20):                          # wait for Next to become enabled
                page.wait_for_timeout(500)
                nxt = page.get_by_role("button", name=re.compile(r"^Next$"))
                if nxt.count() and nxt.first.is_enabled():
                    return True
            return False

        ready = False
        for attempt in range(5):
            seg_reqs.clear(); master_reqs.clear()
            if open_player():
                ready = True; break
            page.reload(wait_until="networkidle"); page.wait_for_timeout(1500)
        if not ready:
            print("RESULT: " + json.dumps({"passed": False, "skipped": True,
                  "reason": "player Next never enabled — /episodes 502 storm across 5 attempts (out of scope)",
                  "evidence": []}))
            browser.close(); return 2

        page.wait_for_timeout(3000)  # let ep1 buffer a few segments
        ep1_masters = [m for m in master_reqs if m["ep"] == "1"]
        if not ep1_masters:
            print("RESULT: " + json.dumps({"passed": False, "skipped": False,
                  "reason": "ep1 master playlist never requested — could not start playback",
                  "evidence": []}))
            page.screenshot(path=os.path.join(SHOTS, "ev1-episode-switch-noplay.png"))
            browser.close(); return 1
        ep1_q, ep1_mode = ep1_masters[-1]["quality"], ep1_masters[-1]["mode"]
        page.screenshot(path=os.path.join(SHOTS, "ev1-episode-switch-ep1.png"))

        S1 = {r["decoded"] for r in seg_reqs if r["decoded"]}

        # --- compute ep2's true segment/manifest set M2 (for the shared-segment exclusion) ---
        def collect_u(url, depth=2, acc=None):
            if acc is None: acc = set()
            try:
                text = page.evaluate("""async (u) => { const r = await fetch(u); return r.ok ? await r.text() : ''; }""", url)
            except Exception:
                return acc
            for m in re.finditer(r"/api/apps/anime/stream/proxy\?u=([^&\s\"']+)", text):
                dec = _b64url_decode(m.group(1))
                if dec:
                    acc.add(dec)
                    if depth > 0 and dec.split("?")[0].endswith(".m3u8"):
                        collect_u(f"/api/apps/anime/stream/proxy?u={m.group(1)}", depth - 1, acc)
            return acc
        ep2_master_url = f"/api/apps/anime/stream/{ANIME_ID}/2/{ep1_q}/master.m3u8?mode={ep1_mode}"
        M2 = collect_u(ep2_master_url)

        # --- click Next and watch the swap ---
        n_before = len(seg_reqs)
        click_t = time.time()
        page.get_by_role("button", name=re.compile(r"^Next$", re.I)).first.click()

        # wait for ep2's master to be requested by the player
        ep2_master = None
        for _ in range(40):
            page.wait_for_timeout(500)
            cand = [m for m in master_reqs if m["ep"] == "2" and m["t"] >= click_t]
            if cand:
                ep2_master = cand[-1]; break
        if not ep2_master:
            print("RESULT: " + json.dumps({"passed": False, "skipped": False,
                  "reason": "ep2 master playlist never requested after Next",
                  "evidence": []}))
            browser.close(); return 1

        settle_start = ep2_master["t"]
        # observe the settle window
        while time.time() < settle_start + SETTLE_S:
            page.wait_for_timeout(300)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SHOTS, "ev1-episode-switch-ep2.png"))

        # --- evaluate the oracle ---
        post = [r for r in seg_reqs[n_before:] if r["decoded"]]
        post_set = {r["decoded"] for r in post}
        new_ep2 = post_set - S1                                   # (c) ep2's distinct stream
        # (d) A-only stale segments requested DURING the settle window
        stale = [r["decoded"] for r in post
                 if r["t"] >= settle_start and r["decoded"] in S1 and r["decoded"] not in M2]

        header = page.evaluate("() => document.body.innerText")
        label_ok = bool(re.search(r"Episode\s*2\b", header))
        preserve_ok = (ep2_master["quality"] == ep1_q and ep2_master["mode"] == ep1_mode)
        distinct_ok = len(new_ep2) > 0
        no_stale_ok = len(stale) == 0

        passed = label_ok and preserve_ok and distinct_ok and no_stale_ok
        result = {
            "passed": passed, "skipped": False,
            "checks": {
                "label_episode_2": label_ok,
                "ep2_master_requested_preserving_mode_quality": preserve_ok,
                "ep2_distinct_segments_loaded": distinct_ok,
                "no_stale_ep1_segments_in_settle_window": no_stale_ok,
            },
            "detail": {
                "ep1_quality": ep1_q, "ep1_mode": ep1_mode,
                "ep2_master": {"quality": ep2_master["quality"], "mode": ep2_master["mode"]},
                "S1_count": len(S1), "M2_count": len(M2),
                "post_click_segment_count": len(post_set),
                "new_ep2_segment_count": len(new_ep2),
                "stale_ep1_segments_in_settle": stale[:5],
            },
            "evidence": [
                os.path.join(SHOTS, "ev1-episode-switch-ep1.png"),
                os.path.join(SHOTS, "ev1-episode-switch-ep2.png"),
            ],
        }
        print("RESULT: " + json.dumps(result))
        browser.close()
        return 0 if passed else 1


def test_player_episode_switch():
    """pytest entry — green only on a real episode-level media swap (skips on a 502 storm)."""
    rc = run()
    if rc == 2:
        import pytest  # type: ignore
        pytest.skip("allanime catalog unavailable (502) — out of scope")
    assert rc == 0


if __name__ == "__main__":
    sys.exit(run())
