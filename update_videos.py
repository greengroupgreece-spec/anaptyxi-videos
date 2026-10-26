
import json
import re
import time
import datetime
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://anaptyxi.forumotion.eu"
FORUM_IDS = [18, 14, 17, 16, 31, 21, 2, 13, 11, 6, 9, 26, 24, 23, 10]

DATA_FILE = Path("videos.json")
ARCHIVE_FILE = Path("video_archive.json")
DAILY_COUNT = 15

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.embeds = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.current = [a["href"], ""]
        if tag == "iframe" and a.get("src"):
            self.embeds.append(a["src"])

    def handle_data(self, data):
        if self.current:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.links.append(tuple(self.current))
            self.current = None

def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")

def parse(html):
    p = PageParser()
    p.feed(html)
    return p

def youtube_id(html):
    patterns = [
        r'youtube(?:-nocookie)?\.com/embed/([\w-]{11})',
        r'youtube\.com/watch\?[^"\s<>]*?v=([\w-]{11})',
        r'youtu\.be/([\w-]{11})',
        r'youtube\.com/shorts/([\w-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None

def load_archive():
    if ARCHIVE_FILE.exists():
        try:
            data = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            pass
    return []

def discover():
    found = []
    seen = set()

    for fid in FORUM_IDS:
        # A limited scan each day keeps requests manageable.
        for offset in [0, 50]:
            url = f"{BASE}/f{fid}p{offset}-forum"
            try:
                page = parse(fetch(url))
            except Exception as exc:
                print("Forum error:", url, exc)
                continue

            for href, title in page.links:
                full = urllib.parse.urljoin(BASE, href)
                match = re.search(r'/t(\d+)', full)
                if not match:
                    continue
                tid = match.group(1)
                if tid in seen:
                    continue
                seen.add(tid)
                title = " ".join(title.split())
                if title:
                    found.append({
                        "id": tid,
                        "title": title,
                        "url": f"{BASE}/t{tid}"
                    })

            time.sleep(0.3)

    return found

def main():
    archive = load_archive()
    known = {str(item["id"]) for item in archive}
    candidates = discover()

    # Inspect a limited number of new topics each run.
    checked = 0
    for topic in candidates:
        if topic["id"] in known:
            continue
        if checked >= 30:
            break
        checked += 1

        try:
            html = fetch(topic["url"])
            vid = youtube_id(html)
            if vid:
                topic["img"] = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
                archive.append(topic)
                known.add(topic["id"])
        except Exception as exc:
            print("Topic error:", topic["url"], exc)

        time.sleep(0.3)

    # Preserve discovered videos, ordered by topic ID.
    archive.sort(key=lambda x: int(x["id"]))
    ARCHIVE_FILE.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Rotate through all archived videos in groups of 15.
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

    DATA_FILE.write_text(
        json.dumps({"videos": selected}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Archived: {len(archive)}; displayed: {len(selected)}")

if __name__ == "__main__":
    main()
