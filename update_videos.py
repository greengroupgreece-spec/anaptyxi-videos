
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
PAGES_PER_RUN = 3
TOPICS_PER_RUN = 30
PAGE_SIZE = 50
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
        if self.current:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.depth -= 1
            if self.depth == 0:
                self.links.append(tuple(self.current))
                self.current = None


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
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
        r'youtube\.com/shorts/([\w-]{11})'
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)

    return None


def forum_url(fid, offset):
    if offset == 0:
        return f"{BASE}/f{fid}-forum"
    return f"{BASE}/f{fid}p{offset}-forum"


def discover_pages(fid):
    html = fetch(forum_url(fid, 0))
    offsets = {0}

    for match in re.finditer(
        rf'/f{fid}p(\d+)(?:-[^"\'\s<>]*)?',
        html
    ):
        offsets.add(int(match.group(1)))

    return sorted(offsets, reverse=True)


def topics_from_page(html):
    parser = PageParser()
    parser.feed(html)

    topics = []
    seen = set()

    for href, title in parser.links:
        full = urllib.parse.urljoin(BASE, href)
        match = re.search(r'/t(\d+)', full)

        if not match:
            continue

        tid = match.group(1)
        title = " ".join(title.split())

        if tid in seen or not title:
            continue

        seen.add(tid)
        topics.append({
            "id": tid,
            "title": title,
            "url": full
        })

    return topics


def main():
    archive = load_json(ARCHIVE_FILE, [])
    state = load_json(STATE_FILE, {})

    known = {str(item["id"]) for item in archive}
    checked = set(str(x) for x in state.get("checked", []))

    if "forums" not in state:
        state["forums"] = {}

    page_budget = PAGES_PER_RUN
    topic_budget = TOPICS_PER_RUN

    for fid in FORUM_IDS:
        if page_budget <= 0 or topic_budget <= 0:
            break

        key = str(fid)
        progress = state["forums"].get(key, {})

        try:
            offsets = discover_pages(fid)
        except Exception as exc:
            print("Forum error:", fid, exc)
            continue

        last_offset = progress.get("last_offset")

        if last_offset is not None:
            offsets = [
                offset for offset in offsets
                if offset < last_offset
            ]

        for offset in offsets:
            if page_budget <= 0 or topic_budget <= 0:
                break

            url = forum_url(fid, offset)

            try:
                html = fetch(url)
                topics = topics_from_page(html)
            except Exception as exc:
                print("Page error:", url, exc)
                continue

            page_budget -= 1

            # Process older topic IDs first.
            topics.sort(key=lambda x: int(x["id"]))

            for topic in topics:
                if topic_budget <= 0:
                    break

                tid = topic["id"]

                if tid in checked or tid in known:
                    continue

                try:
                    html = fetch(topic["url"])
                    vid = youtube_id(html)

                    if vid:
                        topic["img"] = (
                            f"https://img.youtube.com/vi/"
                            f"{vid}/hqdefault.jpg"
                        )
                        archive.append(topic)
                        known.add(tid)

                    checked.add(tid)
                    topic_budget -= 1

                except Exception as exc:
                    print("Topic error:", tid, exc)

                time.sleep(REQUEST_DELAY)

            if topic_budget > 0:
                progress["last_offset"] = offset
                state["forums"][key] = progress

            time.sleep(REQUEST_DELAY)

    archive.sort(key=lambda x: int(x["id"]))

    save_json(ARCHIVE_FILE, archive)

    state["checked"] = sorted(checked, key=int)
    save_json(STATE_FILE, state)

    today = datetime.datetime.now(datetime.timezone.utc).date()
    day_number = (
        today - datetime.date(2026, 10, 27)
    ).days

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
        f"Checked: {len(checked)}"
    )


if __name__ == "__main__":
    main()
