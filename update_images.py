
import json
import re
import time
import datetime
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://anaptyxi.forumotion.eu"
FORUM_IDS = [12, 7, 19]

DATA_FILE = Path("images.json")
ARCHIVE_FILE = Path("image_archive.json")
STATE_FILE = Path("image_scan_state.json")

DAILY_COUNT = 15
PAGES_PER_RUN = 3
TOPICS_PER_RUN = 30
REQUEST_DELAY = 0.4
TIMEOUT = 15

BAD_IMAGE_NAMES = (
    "reddot", "dot", "avatar", "icon", "smil",
    "sprite", "rank", "button", "folder",
    "sticky", "announce", "newpm", "empty",
    "blank", "pixel", "logo", "spacer"
)


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


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.current = [attrs["href"], ""]

    def handle_data(self, data):
        if self.current:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.links.append(tuple(self.current))
            self.current = None


class ImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.post_depth = 0
        self.anchor_stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()

        if tag == "div":
            if self.post_depth:
                self.post_depth += 1
            elif "postbody" in classes:
                self.post_depth = 1

        if tag == "a":
            self.anchor_stack.append(attrs.get("href", ""))

        if tag == "img" and self.post_depth:
            src = (
                attrs.get("data-original")
                or attrs.get("data-src")
                or attrs.get("src")
                or ""
            )

            href = (
                self.anchor_stack[-1]
                if self.anchor_stack else ""
            )

            self.images.append({
                "src": src,
                "classes": classes,
                "href": href
            })

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor_stack:
            self.anchor_stack.pop()

        if tag == "div" and self.post_depth:
            self.post_depth -= 1


def is_bad(src):
    if not src:
        return True

    path = urllib.parse.urlparse(src.lower()).path
    filename = path.rsplit("/", 1)[-1]

    if any(word in filename for word in BAD_IMAGE_NAMES):
        return True

    if "2img.net" in src.lower():
        return True

    if "illiweb" in src.lower():
        return True

    return False


def get_image(html):
    parser = ImageParser()
    parser.feed(html)

    for image in parser.images:
        src = image["src"]
        classes = image["classes"]
        href = image["href"]

        if not (
            "realPostImage" in classes
            or "resize_img" in classes
            or "servimg.com/view" in href
        ):
            continue

        full = urllib.parse.urljoin(BASE, src)

        if not is_bad(full):
            return full

    return None


def forum_url(fid, offset):
    if offset == 0:
        return f"{BASE}/f{fid}-forum"
    return f"{BASE}/f{fid}p{offset}-forum"


def discover_offsets(fid):
    html = fetch(forum_url(fid, 0))
    offsets = {0}

    for match in re.finditer(rf'/f{fid}p(\d+)', html):
        offsets.add(int(match.group(1)))

    return sorted(offsets, reverse=True)


def topics_from_page(html):
    parser = LinkParser()
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

    return sorted(topics, key=lambda x: int(x["id"]))


def main():
    archive = load_json(ARCHIVE_FILE, [])
    state = load_json(STATE_FILE, {})

    # Remove previously collected decorative images.
    archive = [
        item for item in archive
        if not is_bad(item.get("img", ""))
    ]

    known = {str(item["id"]) for item in archive}
    checked = set(str(x) for x in state.get("checked", []))
    forums = state.setdefault("forums", {})

    pages_left = PAGES_PER_RUN
    topics_left = TOPICS_PER_RUN

    for fid in FORUM_IDS:
        if pages_left <= 0 or topics_left <= 0:
            break

        key = str(fid)
        progress = forums.get(key, {})

        try:
            offsets = discover_offsets(fid)
        except Exception as exc:
            print("Forum error:", fid, exc)
            continue

        last = progress.get("last_offset")

        if last is not None:
            offsets = [x for x in offsets if x < last]

        for offset in offsets:
            if pages_left <= 0 or topics_left <= 0:
                break

            try:
                html = fetch(forum_url(fid, offset))
                topics = topics_from_page(html)
            except Exception as exc:
                print("Page error:", fid, offset, exc)
                continue

            pages_left -= 1
            page_complete = True

            for topic in topics:
                if topics_left <= 0:
                    page_complete = False
                    break

                tid = topic["id"]

                if tid in known or tid in checked:
                    continue

                try:
                    html = fetch(topic["url"])
                    img = get_image(html)

                    if img:
                        topic["img"] = img
                        archive.append(topic)
                        known.add(tid)

                    checked.add(tid)
                    topics_left -= 1

                except Exception as exc:
                    print("Topic error:", tid, exc)

                time.sleep(REQUEST_DELAY)

            if page_complete:
                progress["last_offset"] = offset
                forums[key] = progress

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

    save_json(DATA_FILE, {"images": selected})

    print(
        f"Images archived: {len(archive)}; "
        f"displayed: {len(selected)}; "
        f"checked: {len(checked)}"
    )


if __name__ == "__main__":
    main()
