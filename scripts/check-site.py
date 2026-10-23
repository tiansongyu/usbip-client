#!/usr/bin/env python3
"""Check the static site's assets and local documentation targets without network access."""

from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
REPO = "https://github.com/tiansongyu/usbip-client"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            if values["id"] in self.ids:
                self.errors.append(f"Duplicate HTML id: {values['id']}")
            self.ids.add(values["id"])
        for attr in ("src", "href", "srcset"):
            if values.get(attr):
                self.links.append(values[attr])
        if tag == "img" and "alt" not in values:
            self.errors.append("Image missing alt attribute")
        if tag == "img" and not all(k in values for k in ("width", "height")):
            self.errors.append("Image missing intrinsic dimensions")


def markdown_ids(path):
    ids, counts = set(), {}
    fenced = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if fenced or not re.match(r"^#{1,6} ", line):
            continue
        heading = re.sub(r"^#+\s+", "", line).strip().lower()
        heading = re.sub(r"[^\w\-\s]", "", heading)
        slug = heading.replace(" ", "-")
        count = counts.get(slug, 0)
        ids.add(f"{slug}-{count}" if count else slug)
        counts[slug] = count + 1
    return ids


def main():
    page = Page()
    page.feed((SITE / "index.html").read_text())
    errors = page.errors
    count = 0

    def verify(origin, target):
        nonlocal count
        target = unquote(target)
        if target.startswith(REPO + "/blob/main/"):
            target = target.removeprefix(REPO + "/blob/main/")
            origin = ROOT / "README.md"
        elif target.startswith(REPO + "#"):
            target = "README.md#" + target.split("#", 1)[1]
            origin = ROOT / "README.md"
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return
        path = (origin.parent / parsed.path).resolve() if parsed.path else origin
        if not path.is_relative_to(ROOT) or not path.exists():
            errors.append(f"{origin.relative_to(ROOT)}: missing target {target}")
            return
        count += 1
        if parsed.fragment:
            ids = page.ids if path == SITE / "index.html" else markdown_ids(path) if path.suffix == ".md" else set()
            if path == ROOT / "README.md":
                ids.add("readme")  # GitHub repository README wrapper.
            if parsed.fragment not in ids:
                errors.append(f"{origin.relative_to(ROOT)}: missing anchor {target}")

    for target in page.links:
        verify(SITE / "index.html", target)
    for name in ("README.md", "README.zh-CN.md", "docs/native-guide.md", "docs/rust-physical-server.md", "docs/rust-virtual-server.md", "docs/website.md"):
        path = ROOT / name
        for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text()):
            verify(path, target)
    for path in SITE.rglob("*.svg"):
        ET.parse(path)
        count += 1
    for path in SITE.rglob("*"):
        if path.is_symlink():
            errors.append(f"Pages artifact contains a symlink: {path}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"PASS: {count} local asset/link/SVG checks; unique HTML IDs and image metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
