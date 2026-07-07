#!/usr/bin/env python3
"""Download full-resolution KinderTales daily activity images for one date.

The script fetches the daily report page and extracts links from:
  div#gallery_content a[href]

Authentication relies on a valid Cookie header from your browser session.
Provide it via KINDERTALES_COOKIE, --cookie-header, or --cookie-file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import mimetypes
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://app.kindertales.com/index.php"
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
EXIF_WRITABLE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}


class GalleryHrefParser(HTMLParser):
    """Collect href values from anchor tags inside div#gallery_content."""

    def __init__(self) -> None:
        super().__init__()
        self.in_gallery = False
        self.gallery_depth = 0
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "div" and attrs_dict.get("id") == "gallery_content":
            self.in_gallery = True
            self.gallery_depth = 1
            return

        if self.in_gallery:
            if tag == "div":
                self.gallery_depth += 1
            if tag == "a":
                href = attrs_dict.get("href")
                if href:
                    self.hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_gallery:
            return

        if tag == "div":
            self.gallery_depth -= 1
            if self.gallery_depth <= 0:
                self.in_gallery = False
                self.gallery_depth = 0


def parse_report_date(date_str: str | None) -> dt.date:
    """Parse report date from YYYY-MM-DD or MM/DD/YYYY; default is yesterday."""
    if not date_str:
        return dt.date.today() - dt.timedelta(days=1)

    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass

    raise ValueError("--date must be YYYY-MM-DD or MM/DD/YYYY")


def load_cookie_header(args: argparse.Namespace) -> str:
    if args.cookie_header:
        return args.cookie_header.strip()

    if args.cookie_file:
        content = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        if content.lower().startswith("cookie:"):
            content = content.split(":", 1)[1].strip()
        return content

    env_cookie = os.getenv("KINDERTALES_COOKIE", "").strip()
    if env_cookie:
        return env_cookie

    raise ValueError(
        "No cookie found. Provide --cookie-header, --cookie-file, or KINDERTALES_COOKIE."
    )


def build_daily_report_url(base_url: str, cid: str, report_date: dt.date) -> str:
    mmddyyyy = report_date.strftime("%m/%d/%Y")
    return f"{base_url}?pg=dailyreport&cid={cid}&activitydate={mmddyyyy}"


def fetch_text(url: str, cookie: str, user_agent: str, timeout: int) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookie,
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        match = re.search(r"charset=([\w\-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)
        return resp.read().decode(charset, errors="replace")


def extract_gallery_hrefs(html: str, page_url: str) -> list[str]:
    parser = GalleryHrefParser()
    parser.feed(html)

    seen: set[str] = set()
    urls: list[str] = []
    for href in parser.hrefs:
        full = urljoin(page_url, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def guess_extension(content_type: str) -> str:
    content_main = content_type.split(";", 1)[0].strip().lower()
    ext = mimetypes.guess_extension(content_main) if content_main else None
    if not ext:
        return ".jpg"
    return ".jpg" if ext == ".jpe" else ext


def safe_basename_from_url(url: str, fallback_index: int, content_type: str = "") -> str:
    path = unquote(urlsplit(url).path)
    raw_name = os.path.basename(path)

    stem = raw_name or f"image_{fallback_index:03d}"
    if "." in stem:
        base, ext = stem.rsplit(".", 1)
        ext = "." + ext.lower()
    else:
        base = stem
        ext = guess_extension(content_type)

    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or f"image_{fallback_index:03d}"

    short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{fallback_index:03d}_{base}_{short_hash}{ext}"


def download_binary(url: str, cookie: str, user_agent: str, timeout: int) -> tuple[bytes, str]:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Cookie": cookie,
            "Referer": "https://app.kindertales.com/",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def log(debug: bool, message: str) -> None:
    if debug:
        print(message)


def parse_set_location(value: str) -> tuple[float, float]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 2:
        raise ValueError("--set-location must be in format lat,lon")

    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError as exc:
        raise ValueError("--set-location values must be numeric") from exc

    if lat < -90 or lat > 90:
        raise ValueError("--set-location latitude must be between -90 and 90")
    if lon < -180 or lon > 180:
        raise ValueError("--set-location longitude must be between -180 and 180")

    return lat, lon


def apply_exif_metadata(
    file_path: Path,
    exif_dt: dt.datetime,
    location: tuple[float, float] | None,
) -> bool:
    if file_path.suffix.lower() not in EXIF_WRITABLE_EXTENSIONS:
        return False

    exif_dt_str = exif_dt.strftime("%Y:%m:%d %H:%M:%S")
    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-DateTimeOriginal={exif_dt_str}",
        f"-CreateDate={exif_dt_str}",
        f"-ModifyDate={exif_dt_str}",
        f"-FileModifyDate={exif_dt_str}",
        f"-FileCreateDate={exif_dt_str}",
    ]

    if location is not None:
        lat, lon = location
        lat_ref = "N" if lat >= 0 else "S"
        lon_ref = "E" if lon >= 0 else "W"
        cmd.extend(
            [
                f"-GPSLatitude={abs(lat)}",
                f"-GPSLatitudeRef={lat_ref}",
                f"-GPSLongitude={abs(lon)}",
                f"-GPSLongitudeRef={lon_ref}",
            ]
        )

    cmd.append(str(file_path))
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return True


def iter_downloads(
    urls: Iterable[str],
    out_dir: Path,
    cookie: str,
    user_agent: str,
    timeout: int,
    file_timestamp: float,
    exif_datetime: dt.datetime | None,
    exif_location: tuple[float, float] | None,
    overwrite: bool,
    dry_run: bool,
    debug: bool,
) -> tuple[int, int]:
    ok = 0
    failed = 0

    for idx, url in enumerate(urls, start=1):
        try:
            if dry_run:
                print(f"DRY RUN {idx:03d}: {url}")
                ok += 1
                continue

            data, content_type = download_binary(url, cookie, user_agent, timeout)
            filename = safe_basename_from_url(url, idx, content_type)
            target = out_dir / filename

            if target.exists() and not overwrite:
                log(debug, f"SKIP existing: {target}")
                ok += 1
                continue

            target.write_bytes(data)
            os.utime(target, (file_timestamp, file_timestamp))

            if exif_datetime is not None:
                updated = apply_exif_metadata(target, exif_datetime, exif_location)
                if updated:
                    log(debug, f"EXIF UPDATED: {target}")
                else:
                    log(debug, f"EXIF SKIP (unsupported format): {target}")

            log(debug, f"DOWNLOADED {target} ({len(data)} bytes)")
            ok += 1
        except Exception as exc:
            print(f"FAILED {idx:03d}: {url}\n  reason: {exc}")
            failed += 1

    return ok, failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download KinderTales activity images for one day")
    parser.add_argument("--cid", required=True, help="Child ID (cid query parameter)")
    parser.add_argument(
        "--date",
        default=None,
        help="Report date in YYYY-MM-DD or MM/DD/YYYY (default: yesterday)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Daily report page URL without query string",
    )
    parser.add_argument(
        "--output-dir",
        default="downloads",
        help="Root output directory (date folder is created inside)",
    )
    parser.add_argument("--cookie-header", default=None, help="Full Cookie header value")
    parser.add_argument("--cookie-file", default=None, help="Path to file containing Cookie header value")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent header")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout seconds")
    parser.add_argument(
        "--set-date-exif",
        action="store_true",
        help="Write EXIF date using report date (12:00:00 local time)",
    )
    parser.add_argument(
        "--set-location",
        default=None,
        help="Write GPS EXIF location as lat,lon (example: 49.2827,-123.1207)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", help="Only list links; do not download")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        report_date = parse_report_date(args.date)
        cookie = load_cookie_header(args)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2

    page_url = build_daily_report_url(args.base_url, args.cid, report_date)
    date_folder = report_date.strftime("%Y-%m-%d")
    output_dir = Path(args.output_dir) / date_folder
    report_datetime = dt.datetime.combine(report_date, dt.time(12, 0, 0))
    report_timestamp = report_datetime.timestamp()

    exif_datetime: dt.datetime | None = None
    exif_location: tuple[float, float] | None = None
    if args.set_date_exif:
        exif_datetime = report_datetime

    if args.set_location:
        try:
            exif_location = parse_set_location(args.set_location)
        except ValueError as exc:
            print(f"CONFIG ERROR: {exc}")
            return 2
        if exif_datetime is None:
            exif_datetime = report_datetime

    if exif_datetime is not None:
        try:
            subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
        except FileNotFoundError:
            print(
                "CONFIG ERROR: exiftool is required for EXIF writing. Install exiftool and retry."
            )
            return 2
        except subprocess.CalledProcessError as exc:
            print(f"CONFIG ERROR: failed to run exiftool: {exc}")
            return 2

    print(f"Fetching report page: {page_url}")
    try:
        html = fetch_text(page_url, cookie, args.user_agent, args.timeout)
    except Exception as exc:
        print(f"FETCH ERROR: {exc}")
        return 1

    urls = extract_gallery_hrefs(html, page_url)
    print(f"Found {len(urls)} full-resolution gallery links")

    if not urls:
        print("No gallery links found. The page may have changed or auth expired.")
        return 1

    ensure_dir(output_dir)
    ok, failed = iter_downloads(
        urls=urls,
        out_dir=output_dir,
        cookie=cookie,
        user_agent=args.user_agent,
        timeout=args.timeout,
        file_timestamp=report_timestamp,
        exif_datetime=exif_datetime,
        exif_location=exif_location,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        debug=args.debug,
    )

    print(f"Done. Success: {ok}, Failed: {failed}, Folder: {output_dir}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
