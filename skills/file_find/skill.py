import os
import sys
import time
from pathlib import Path

from rapidfuzz.fuzz import token_sort_ratio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import ALLOWED_ROOTS, safe_emit

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt"}
SPREADSHEET_EXTS = {".xls", ".xlsx", ".csv", ".ods"}
VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}

QUERY_EXT_HINTS = {
    "photo": IMAGE_EXTS, "image": IMAGE_EXTS, "picture": IMAGE_EXTS, "screenshot": IMAGE_EXTS,
    "doc": DOC_EXTS, "document": DOC_EXTS, "text": DOC_EXTS, "note": DOC_EXTS,
    "spreadsheet": SPREADSHEET_EXTS, "budget": SPREADSHEET_EXTS, "excel": SPREADSHEET_EXTS,
    "video": VIDEO_EXTS, "movie": VIDEO_EXTS,
    "audio": AUDIO_EXTS, "music": AUDIO_EXTS, "song": AUDIO_EXTS,
}

SCORE_WEIGHT_FUZZY = 0.7
SCORE_WEIGHT_RECENCY = 0.15
SCORE_WEIGHT_SUBSTRING = 0.1
SCORE_WEIGHT_EXT = 0.05
MIN_SCORE_THRESHOLD = 0.45
MAX_FILES_SCANNED = 50000


def _recent_boost(mtime, now):
    age_days = (now - mtime) / 86400
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.7
    if age_days <= 90:
        return 0.4
    return 0.0


def _score_file(query, name_lower, ext_lower, stat, now):
    substring = 1.0 if query in name_lower else 0.0
    raw_fuzzy = token_sort_ratio(query, name_lower)
    if substring == 0.0 and raw_fuzzy < 80:
        return 0.0
    fuzzy = raw_fuzzy / 100.0
    recency = _recent_boost(stat.st_mtime, now)
    ext_rel = 0.0
    for keyword, exts in QUERY_EXT_HINTS.items():
        if keyword in query and ext_lower in exts:
            ext_rel = 1.0
            break
    return (
        SCORE_WEIGHT_FUZZY * fuzzy
        + SCORE_WEIGHT_RECENCY * recency
        + SCORE_WEIGHT_SUBSTRING * substring
        + SCORE_WEIGHT_EXT * ext_rel
    )


def _url_to_path(url):
    if not url:
        return None
    url = url.replace("\\", "/")
    if url.startswith("file:"):
        url = url[5:]
        if url.startswith("//"):
            url = url[2:]
    from urllib.parse import unquote
    url = unquote(url)
    p = Path(url)
    try:
        return p.resolve()
    except OSError:
        return p


def _is_within_roots(path):
    resolved = Path(path).resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _query_winsearch(query, max_results):
    try:
        import win32com.client
    except ImportError:
        return None

    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows'")

        sanitized = query.replace("'", "").replace('"', "")
        sql = (
            f"SELECT TOP {max_results * 3} "
            f"System.ItemName, System.ItemUrl, System.Size, System.DateModified "
            f"FROM SystemIndex WHERE System.ItemName LIKE '%{sanitized}%'"
        )
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(sql, conn)

        results = []
        while not rs.EOF:
            url = rs.Fields.Item("System.ItemUrl").Value or ""
            path = _url_to_path(url)
            if path and _is_within_roots(path):
                try:
                    stat = path.stat()
                    size = stat.st_size
                    mtime = stat.st_mtime
                except OSError:
                    size = rs.Fields.Item("System.Size").Value or 0
                    mtime = 0
                results.append({"path": path, "size": size, "mtime": mtime})
            rs.MoveNext()

        rs.Close()
        conn.Close()
        return results if results else None

    except Exception:
        return None


def _scan_files(query, max_results):
    now = time.time()
    query_lower = query.lower()

    indexed = _query_winsearch(query, max_results * 3)
    if indexed is not None:
        candidates = []
        for item in indexed:
            score = _score_file(query_lower, item["path"].stem.lower(), item["path"].suffix.lower(), type("S", (), {"st_mtime": item["mtime"], "st_size": item["size"]})(), now)
            item["score"] = max(score, 0.6)
            candidates.append(item)
        candidates.sort(key=lambda c: c["score"], reverse=True)
        if candidates:
            return candidates[:max_results]

    candidates = []
    files_scanned = 0
    for root in ALLOWED_ROOTS:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
            for fname in filenames:
                if fname.startswith((".", "_")):
                    continue
                files_scanned += 1
                if files_scanned > MAX_FILES_SCANNED:
                    return candidates
                fpath = Path(dirpath) / fname
                try:
                    resolved = fpath.resolve()
                    stat = resolved.stat()
                except OSError:
                    continue
                score = _score_file(query_lower, resolved.stem.lower(), resolved.suffix.lower(), stat, now)
                if score < MIN_SCORE_THRESHOLD:
                    continue
                candidates.append({
                    "path": resolved,
                    "score": score,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_results]


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if not ns.query or not ns.query.strip():
        safe_emit(False, error="query is required")
    max_results = ns.max_results if ns.max_results is not None else 5
    if max_results < 0:
        max_results = 0
    try:
        candidates = _scan_files(ns.query.strip(), max_results)
    except Exception as e:
        safe_emit(False, error=f"scan failed: {e}")
    if not candidates:
        safe_emit(True, {"matches": [], "query": ns.query}, display_text="no files found")
    ambiguous = False
    if len(candidates) >= 2:
        top = candidates[0]["score"]
        second = candidates[1]["score"]
        if top > 0 and (top - second) / top < 0.10:
            ambiguous = True
    matches = []
    for c in candidates:
        matches.append({
            "absolute_path": str(c["path"]),
            "filename": c["path"].name,
            "score": round(c["score"], 3),
            "size": c["size"],
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c["mtime"])),
        })
    status = "ambiguous" if ambiguous else "ok"
    best = matches[0]["filename"] if matches else "none"
    display = f"{len(matches)} match(es) for '{ns.query}'; best: {best}"
    safe_emit(True, {"matches": matches, "query": ns.query}, status=status, display_text=display)


if __name__ == "__main__":
    main()
