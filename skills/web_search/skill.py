import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for


def clean_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).replace("\n", " ").strip()


def unwrap_url(url):
    if url.startswith("//"):
        url = "https:" + url
    if "uddg=" in url:
        q = parse_qs(urlparse(url).query)
        if "uddg" in q:
            return unquote(q["uddg"][0])
    return url


def search(query, max_results):
    import requests

    resp = requests.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=20,
    )
    resp.raise_for_status()
    html = resp.text
    titles = re.findall(
        r'<a[^>]*href="([^"]+)"[^>]*class=["\']result-link["\'][^>]*>(.*?)</a>', html, re.S | re.I
    ) or re.findall(
        r'<a[^>]*class=["\']result-link["\'][^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I
    )
    snippets = re.findall(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", html, re.S | re.I)
    results = []
    for i, (href, title) in enumerate(titles):
        if i >= max_results:
            break
        snippet = clean_html(snippets[i]) if i < len(snippets) else ""
        results.append({"title": clean_html(title), "url": unwrap_url(href), "snippet": snippet})
    return results


def fetch_page_text(url, max_chars=1500):
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "form", "noscript", "iframe", "svg", "button",
                     "noscript", "meta", "link"]):
        tag.decompose()

    for tag in soup.find_all(class_=re.compile(r"(sidebar|menu|nav|footer|header|cookie|banner|popup|modal|ad[-_])", re.I)):
        tag.decompose()

    for tag in soup.find_all(id=re.compile(r"(sidebar|menu|nav|footer|header|cookie|banner|popup|modal|ad[-_])", re.I)):
        tag.decompose()

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile(r"(content|post|entry|article|body|text)[-_]", re.I))
        or soup.find("body")
    )
    if main is None:
        return ""

    paragraphs = main.find_all("p")
    if paragraphs:
        text = " ".join(p.get_text(separator=" ", strip=True) for p in paragraphs)
    else:
        text = main.get_text(separator=" ", strip=True)

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if not ns.query:
        emit(False, error="query is required")
    try:
        results = search(ns.query, ns.max)
    except Exception as e:
        emit(False, error=f"search failed: {e}")
    if not results:
        emit(False, error="no results found")

    top = results[0]
    url = top["url"]

    try:
        page_text = fetch_page_text(url)
    except Exception:
        page_text = ""

    if page_text:
        emit(True, f"{page_text}\n\n(source: {url})")
    else:
        content = top["snippet"] or top["title"]
        emit(True, f"{content}\n\n(source: {url})")


if __name__ == "__main__":
    main()