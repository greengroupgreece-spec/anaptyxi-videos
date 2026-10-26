
import json
import re
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://anaptyxi.forumotion.eu"
FORUM_IDS = [18, 14, 17, 16, 31, 21, 2, 13, 11, 6, 9, 26, 24, 23, 10]

DATA_FILE = Path("videos.json")
ARCHIVE_FILE = Path("video_archive.json")
STATE_FILE = Path("video_scan_state.json")

DAILY_COUNT = 15
FORUM_PAGES_PER_RUN = 3
TOPICS_PER_RUN = 25
REQUEST_DELAY = 0.4
TIMEOUT = 10


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            if self.current is None:
                self.current = [attrs["href"], ""]
                self.depth = 1
            else:
                self.depth += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.depth -= 1
            if self.depth <= 0:
                self.links.append(tuple(self.current))
                self.current = None
                self.depth = 0


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html"
        }
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return default


def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def youtube_id(html):
    patterns = [
        r'youtube(?:-nocookie)?\.com/embed/([\w-]{11})',
        r'youtube\.com/watch\?[^"\s<>]*?v=([\w-]{11})',
        r'youtu\.be/([\w-]{11})',
        r'youtube\.com/shorts/([\w-]{11})',
        r'youtube\.com/v/([\w-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_topics(html):
    parser = PageParser()
    parser.feed(html)

    topics = []
    seen = set()

    for href, title in parser.links:
        url = urllib.parse.urljoin(BASE, href)
        parsed = urllib.parse.urlparse(url)

        if parsed.netloc != "anaptyxi.forumotion.eu":
            continue

        match = re.match(r"/t(\d+)(?:[-/]|$)", parsed.path)
        if not match:
            continue

        tid = match.group(1)
        title = " ".join(title.split())

        if not title or tid in seen:
            continue

        seen.add(tid)
        topics.append({
            "id": tid,
            "title": title,
            "url": urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path,
                 "", "", "")
            )
        })

    return topics


def main():
    archive = load_json(ARCHIVE_FILE, [])
    state = load_json(STATE_FILE, {})

    if not isinstance(archive, list):
        archive = []
    if not isinstance(state, dict):
        state = {}

    checked = set(map(str, state.get("checked", [])))
    pending = state.get("pending", [])
    page_index = int(state.get("page_index", 0))

    if not isinstance(pending, list):
        pending = []

    known = {
        str(item["id"])
        for item in archive
        if isinstance(item, dict) and "id" in item
    }

    # Build a rotating list of forum pages.
    pages = []
    for offset in [0, 50, 100, 150, 200]:
        for fid in FORUM_IDS:
            pages.append(f"{BASE}/f{fid}p{offset}-forum")

    # Discover a few pages per execution.
    for _ in range(FORUM_PAGES_PER_RUN):
        url = pages[page_index % len(pages)]

        try:
            html = fetch(url)
            topics = extract_topics(html)

            existing = {
                str(item["id"])
                for item in pending
                if isinstance(item, dict) and "id" in item
            }

            for topic in topics:
                tid = topic["id"]
                if tid not in checked and tid not in known and tid not in existing:
                    pending.append(topic)
                    existing.add(tid)

            print("Scanned:", url, "topics:", len(topics), flush=True)

        except Exception as exc:
            print("Forum error:", url, exc, flush=True)

        page_index += 1
        time.sleep(REQUEST_DELAY)

    # Inspect a limited number of topics.
    inspected = 0
    retry = []

    while pending and inspected < TOPICS_PER_RUN:
        topic = pending.pop(0)
        tid = str(topic["id"])

        if tid in checked or tid in known:
            continue

        inspected += 1

        try:
            html = fetch(topic["url"])
            vid = youtube_id(html)

            if vid:
                topic["img"] = (
                    f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
                )
                archive.append(topic)
                known.add(tid)
                print("Found video:", topic["title"], flush=True)

            checked.add(tid)

        except Exception as exc:
            print("Topic error:", topic["url"], exc, flush=True)
            retry.append(topic)

        time.sleep(REQUEST_DELAY)

    pending.extend(retry)

    # Preserve every discovered video.
    archive.sort(key=lambda item: int(item["id"]))
    save_json(ARCHIVE_FILE, archive)

    # Save progress for the next daily run.
    save_json(STATE_FILE, {
        "checked": sorted(checked, key=int),
        "pending": pending,
        "page_index": page_index
    })

    # Rotate through the archive, 15 videos per day.
    today = datetime.datetime.now(datetime.timezone.utc).date()
    day_number = (today - datetime.date(2026, 10, 27)).days

    if archive:
        start = (day_number * DAILY_COUNT) % len(archive)
        selected = [
            archive[(start + i) % len(archive)]
            for i in range(min(DAILY_COUNT, len(archive)))
        ]
    else:
        selected = []

    save_json(DATA_FILE, {"videos": selected})

    print(
        f"Archived: {len(archive)}; "
        f"Displayed: {len(selected)}; "
        f"Pending: {len(pending)}",
        flush=True
    )


if __name__ == "__main__":
    main()
