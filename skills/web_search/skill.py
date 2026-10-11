import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
SNIPPET_CHARS = 400
MAX_RESULTS_CAP = 10


def _text(node):
    return node.get_text(" ", strip=True) if node else ""


def _trim(text):
    return " ".join(text.split())[:SNIPPET_CHARS]


def _flatten(value):
    if isinstance(value, list):
        return "".join(
            part.get("value", "") if isinstance(part, dict) else str(part)
            for part in value
        )
    return str(value or "")


def search_rightdao(query, max_results):
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(
        "https://rightdao.com/search",
        params={"q": query},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    # rightdao serves text/html with no charset, so requests guesses
    # ISO-8859-1 and mangles every non-ASCII character in a snippet
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select("div.item"):
        link = item.find("a", href=True)
        description = item.select_one("div.description")
        if not link or not link["href"].startswith("http") or not description:
            continue
        results.append({
            "title": _text(item.select_one("div.title")) or _text(link),
            "url": _text(item.select_one("div.info span.url")) or link["href"],
            "snippet": _trim(_text(description)),
        })
        if len(results) >= max_results:
            break
    return results


def search_mwmbl(query, max_results):
    import requests

    resp = requests.get(
        "https://api.mwmbl.org/api/v1/search/",
        params={"s": query},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = []
    for item in resp.json():
        url = str(item.get("url") or "")
        title = _flatten(item.get("title")).strip()
        if not url.startswith("http") or not title:
            continue
        results.append({
            "title": title,
            "url": url,
            "snippet": _trim(_flatten(item.get("extract"))),
        })
        if len(results) >= max_results:
            break
    return results


def search(query, max_results):
    failures = []
    for name, backend in (("rightdao", search_rightdao), ("mwmbl", search_mwmbl)):
        try:
            results = backend(query, max_results)
        except Exception as e:
            failures.append(f"{name}: {e}")
            continue
        if results:
            return results
        failures.append(f"{name}: no results")
    raise RuntimeError("; ".join(failures))


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    max_results = max(1, min(ns.max or 1, MAX_RESULTS_CAP))
    try:
        results = search(ns.query, max_results)
    except Exception as e:
        emit(False, error=f"search failed: {e}")
        return
    if not results:
        emit(False, error=f"no results found for {ns.query!r}")
        return
    emit(True, {"query": ns.query, "results": results})


if __name__ == "__main__":
    main()