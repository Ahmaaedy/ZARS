import json
import os
import re
import subprocess
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

LIBRARY = Path(__file__).resolve().parents[2] / "config" / "songs.json"

# Below this a library hit is treated as a miss and the caller is told so,
# rather than playing whatever happened to score highest.
MATCH_FLOOR = 0.72
NEAR_MISS_RATIO = 0.6
MAX_CANDIDATES = 5

# Spotify ids are 22 base62 chars. Anything else came from somewhere other than
# a track link and never reaches Start-Process.
ID_RE = re.compile(r"^spotify:track:[A-Za-z0-9]{22}$")
BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Pulled out of the search results by class *substring* and ARIA role rather
# than by Spotify's hashed CSS-module names, which are regenerated per deploy
# and are not a stable contract.
ROWS_JS = """
() => {
  const out = [];
  const txt = el => (el && el.innerText ? el.innerText.replace(/\\s+/g, " ").trim() : "");
  // Spotify renders a hidden copy of every string for screen readers, so the
  // first match is usually the empty one. Take the first that actually has text.
  const firstText = (root, sel) => {
    for (const el of root.querySelectorAll(sel)) {
      const t = txt(el);
      if (t) return t;
    }
    return "";
  };
  for (const row of document.querySelectorAll('[role="row"]')) {
    const link = row.querySelector('a[href*="/track/"]');
    if (!link) continue;
    const m = link.getAttribute("href").match(/\\/track\\/([A-Za-z0-9]{22})/);
    if (!m) continue;
    const cells = [...row.querySelectorAll('[role="gridcell"]')];
    const cell = cells[1];
    if (!cell) continue;
    out.push({
      id: m[1],
      title: firstText(cell, 'div[class*="text-body-medium"]'),
      artist: firstText(cell, 'a[href*="/artist/"], span[class*="text-small"], a[class*="text-small"]'),
      album: txt(cells[2]),
      duration: txt(cells[3])
    });
  }
  return out;
}
"""


def _note(message):
    print(message, file=sys.stderr)


def _norm(text):
    """Lowercase and drop punctuation, keeping letters from every script.

    core.fuzzy_match.normalize reduces to [a-z0-9\\s], which would erase
    "アイドル" and the accent in "Oh Qué Será?" -- both are real rows in the
    library, so this keeps \\w and casefolds instead.
    """
    return " ".join(re.sub(r"[^\w\s]", " ", text or "").casefold().split())


def _load_library():
    if not LIBRARY.exists():
        raise RuntimeError(f"song library not found: {LIBRARY}")
    data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"{LIBRARY} is not a JSON array")
    return data


def _score(query, entry):
    """How well a library row answers the request. 0.0 means no relationship."""
    q = _norm(query)
    title = _norm(entry.get("title"))
    if not q or not title:
        return 0.0

    ratio = SequenceMatcher(None, q, title).ratio()
    title_tokens = set(title.split())
    query_tokens = set(q.split())
    overlap = len(query_tokens & title_tokens) / len(title_tokens)

    artist = _norm(entry.get("artist"))
    bonus = 0.0
    if artist and artist in q:
        bonus = 0.15
    elif artist and any(word in q for word in artist.split() if len(word) > 3):
        bonus = 0.07

    return min(1.0, ratio * 0.65 + overlap * 0.25 + bonus)


def _rank(query, library):
    scored = [(_score(query, row), row) for row in library]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def _row_label(row):
    return f"{row.get('title', '?')} - {row.get('artist', '?')}"


def _play(spotify_id):
    if not ID_RE.match(spotify_id or ""):
        raise RuntimeError(f"refusing to play malformed spotify id: {spotify_id!r}")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f'Start-Process "{spotify_id}"'],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _scrape_candidates(query, limit=MAX_CANDIDATES):
    """Anonymous lookup: open Spotify's own search page and read the track links.

    No API key and no Premium account, which the Web API now demands. Uses the
    /tracks sub-page on purpose -- the combined /search page falls back to a
    generic preview catalogue when nobody is logged in, and those are wrong
    answers rather than missing ones.
    """
    from urllib.parse import quote

    from playwright.sync_api import sync_playwright

    url = "https://open.spotify.com/search/" + quote(query) + "/tracks"
    _note(f"looking up {query!r} on Spotify (this opens a hidden browser)...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="en-US")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # The consent dialog keeps the results from hydrating if left up.
            for label in ("Accept all", "Accept", "Agree", "Allow all"):
                try:
                    button = page.get_by_role("button", name=label)
                    if button.count():
                        button.first.click(timeout=2000)
                        break
                except Exception:
                    continue

            page.wait_for_selector('[role="row"] a[href*="/track/"]', timeout=20000)
            rows = page.evaluate(ROWS_JS)
        finally:
            browser.close()

    candidates = []
    seen = set()
    for row in rows:
        track_id = str(row.get("id") or "")
        if not BARE_ID_RE.match(track_id):
            continue
        title = _norm(row.get("title"))
        artist = _norm(row.get("artist"))
        if not title:
            continue
        # The same track is listed once per compilation it appears on, so
        # collapse on (title, artist) and keep the highest-ranked copy.
        key = (title, artist)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "track_id": track_id,
            "title": row.get("title", ""),
            "artist": row.get("artist", ""),
            "album": row.get("album", ""),
            "duration": row.get("duration", ""),
        })
        if len(candidates) >= limit:
            break
    return candidates


def _append_row(row):
    """Add a row to the library, keeping the file's one-object-per-line layout.

    Rewriting it through json.dumps(indent=2) would reflow all 118 existing
    rows and bury the actual change in noise.
    """
    text = LIBRARY.read_text(encoding="utf-8")
    data = json.loads(text)
    if any((r.get("spotify_id") or "") == row["spotify_id"] for r in data):
        return False

    stripped = text.rstrip()
    if not stripped.endswith("]"):
        raise RuntimeError(f"{LIBRARY} is not a JSON array")

    body = stripped[:-1].rstrip()
    separator = "" if body.endswith("[") else ","
    # Compact separators, because that is how the rest of the file is written.
    compact = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    updated = f"{body}{separator}\n  {compact}\n]\n"
    json.loads(updated)  # refuse to write anything we cannot read back

    tmp = LIBRARY.with_suffix(".json.tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, LIBRARY)
    return True


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        song = (ns.song or "").strip()
        if not song:
            emit(False, error="song is required")
            return

        if ns.pick and not ns.search:
            emit(False, error="--pick only applies together with --search")
            return

        library = _load_library()
        ranked = _rank(song, library)
        best_score, best = ranked[0] if ranked else (0.0, None)

        if best_score >= MATCH_FLOOR and (best.get("spotify_id") or ""):
            _play(best["spotify_id"])
            emit(True, {
                "status": "played",
                "source": "library",
                "title": best.get("title"),
                "artist": best.get("artist"),
                "spotify_id": best["spotify_id"],
                "score": round(best_score, 2),
            })
            return

        if not ns.search:
            near = [
                {"title": row.get("title"), "artist": row.get("artist"), "score": round(score, 2)}
                for score, row in ranked[:3]
                if score >= NEAR_MISS_RATIO
            ]
            emit(True, {
                "status": "not_in_library",
                "query": song,
                "best_guess": _row_label(best) if best_score >= NEAR_MISS_RATIO else None,
                "near_misses": near,
                "suggestion": f"re-run with --search to look {song!r} up on Spotify",
            })
            return

        candidates = _scrape_candidates(song)
        if not candidates:
            emit(False, error=f"no Spotify results for {song!r}")
            return

        if not ns.pick:
            emit(True, {
                "status": "candidates",
                "query": song,
                "candidates": candidates,
                "suggestion": f"re-run with --pick N (1-{len(candidates)}) to play and save that one",
            })
            return

        if not 1 <= ns.pick <= len(candidates):
            emit(False, error=f"--pick must be between 1 and {len(candidates)}")
            return

        chosen = candidates[ns.pick - 1]
        track_id = f"spotify:track:{chosen['track_id']}"
        _play(track_id)

        note = f"added by play_skill on {date.today().isoformat()}"
        if chosen.get("album"):
            note += f"; from {chosen['album']}"
        added = _append_row({
            "artist": chosen.get("artist", ""),
            "title": chosen.get("title", ""),
            "spotify_id": track_id,
            "url": f"https://open.spotify.com/track/{chosen['track_id']}",
            "confidence": "medium",
            "note": note,
        })

        emit(True, {
            "status": "played",
            "source": "spotify_search",
            "title": chosen.get("title"),
            "artist": chosen.get("artist"),
            "spotify_id": track_id,
            "saved_to_library": added,
            "library": str(LIBRARY),
        })
    except Exception as e:
        emit(False, error=str(e))


if __name__ == "__main__":
    main()