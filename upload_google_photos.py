#!/usr/bin/env python3
"""Upload local photos to Google Photos.

Supports uploading:
- one file via --file
- all images from a folder via --folder

First run performs OAuth login in browser and stores token in a local file.

Optional: --set-date writes photo metadata date before upload so Google Photos
can use that date in item details.
Optional: --set-location writes GPS EXIF data before upload.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError as exc:
    print(
        "Missing dependency. Install with: pip install google-auth google-auth-oauthlib"
    )
    raise SystemExit(2) from exc

try:
    import requests
except ImportError as exc:  # pragma: no cover
    print("Missing dependency. Install with: pip install requests")
    raise SystemExit(2) from exc

SCOPE = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]
UPLOAD_URL = "https://photoslibrary.googleapis.com/v1/uploads"
BATCH_CREATE_URL = "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate"
DEFAULT_CLIENT_SECRET = "google_client_secret.json"
DEFAULT_TOKEN = "google_photos_token.json"
ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".mp4"
}
METADATA_DATE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}


def log(debug: bool, message: str) -> None:
    if debug:
        print(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload photos to Google Photos")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", help="Single image file to upload")
    source_group.add_argument("--folder", help="Folder containing image files to upload")

    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan folder for images (only with --folder)",
    )
    parser.add_argument(
        "--client-secret",
        default=DEFAULT_CLIENT_SECRET,
        help="OAuth client secret JSON downloaded from Google Cloud",
    )
    parser.add_argument(
        "--token-file",
        default=DEFAULT_TOKEN,
        help="Path to store OAuth user token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show files that would be uploaded without uploading",
    )
    parser.add_argument(
        "--set-date",
        default=None,
        help="Set photo metadata date before upload (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
    )
    parser.add_argument(
        "--set-location",
        default=None,
        help="Set GPS EXIF location before upload as lat,lon (example: 49.2827,-123.1207)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    return parser.parse_args()


def parse_set_date(value: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                return dt.datetime.combine(parsed.date(), dt.time(12, 0, 0))
            return parsed
        except ValueError:
            pass

    raise ValueError("--set-date must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")


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


def get_credentials(client_secret_path: Path, token_path: Path) -> Credentials:
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPE)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not client_secret_path.exists():
            raise FileNotFoundError(
                f"Client secret file not found: {client_secret_path}. "
                "Create an OAuth Desktop app in Google Cloud and download JSON."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPE)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_EXTENSIONS


def collect_files(file_arg: str | None, folder_arg: str | None, recursive: bool) -> list[Path]:
    if file_arg:
        file_path = Path(file_arg).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise ValueError(f"Not a file: {file_path}")
        if not is_image_file(file_path):
            raise ValueError(f"Unsupported image extension: {file_path.suffix}")
        return [file_path]

    if not folder_arg:
        raise ValueError("Either --file or --folder must be provided")

    folder_path = Path(folder_arg).expanduser().resolve()
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    if not folder_path.is_dir():
        raise ValueError(f"Not a folder: {folder_path}")

    iterator: Iterable[Path]
    if recursive:
        iterator = folder_path.rglob("*")
    else:
        iterator = folder_path.glob("*")

    files = sorted(p for p in iterator if p.is_file() and is_image_file(p))
    return files


def apply_metadata_date(file_path: Path, target_dt: dt.datetime) -> bool:
    if file_path.suffix.lower() not in METADATA_DATE_EXTENSIONS:
        return False

    exif_dt = target_dt.strftime("%Y:%m:%d %H:%M:%S")
    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-DateTimeOriginal={exif_dt}",
        f"-CreateDate={exif_dt}",
        f"-ModifyDate={exif_dt}",
        f"-FileModifyDate={exif_dt}",
        f"-FileCreateDate={exif_dt}",
        str(file_path),
    ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return True


def apply_metadata_location(file_path: Path, latitude: float, longitude: float) -> bool:
    if file_path.suffix.lower() not in METADATA_DATE_EXTENSIONS:
        return False

    lat_ref = "N" if latitude >= 0 else "S"
    lon_ref = "E" if longitude >= 0 else "W"
    lat_abs = abs(latitude)
    lon_abs = abs(longitude)

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-GPSLatitude={lat_abs}",
        f"-GPSLatitudeRef={lat_ref}",
        f"-GPSLongitude={lon_abs}",
        f"-GPSLongitudeRef={lon_ref}",
        str(file_path),
    ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return True


def build_auth_headers(creds: Credentials) -> dict[str, str]:
    if not creds.valid:
        creds.refresh(Request())

    return {
        "Authorization": f"Bearer {creds.token}",
    }


def upload_bytes(creds: Credentials, file_path: Path) -> str:
    auth_headers = build_auth_headers(creds)
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    headers = {
        **auth_headers,
        "Content-Type": "application/octet-stream",
        "X-Goog-Upload-Content-Type": content_type,
        "X-Goog-Upload-Protocol": "raw",
    }

    data = file_path.read_bytes()
    response = requests.post(UPLOAD_URL, headers=headers, data=data, timeout=60)
    response.raise_for_status()
    return response.text


def batch_create_media_items(creds: Credentials, upload_tokens: list[tuple[Path, str]]) -> list[dict]:
    auth_headers = build_auth_headers(creds)

    payload = {
        "newMediaItems": [
            {
                "description": "Uploaded by script",
                "simpleMediaItem": {
                    "fileName": file_path.name,
                    "uploadToken": token,
                },
            }
            for file_path, token in upload_tokens
        ]
    }

    response = requests.post(BATCH_CREATE_URL, headers=auth_headers, json=payload, timeout=60)
    response.raise_for_status()
    body = response.json()
    return body.get("newMediaItemResults", [])


def chunked(items: list[tuple[Path, str]], size: int) -> Iterable[list[tuple[Path, str]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> int:
    args = parse_args()

    try:
        files = collect_files(args.file, args.folder, args.recursive)
    except Exception as exc:
        print(f"INPUT ERROR: {exc}")
        return 2

    if not files:
        print("No images found to upload.")
        return 1

    print(f"Found {len(files)} image(s)")
    for path in files:
        log(args.debug, f" - {path}")

    if args.dry_run:
        print("Dry run enabled. Exiting without upload.")
        return 0

    set_date_dt: dt.datetime | None = None
    set_location: tuple[float, float] | None = None
    if args.set_date:
        try:
            set_date_dt = parse_set_date(args.set_date)
        except ValueError as exc:
            print(f"INPUT ERROR: {exc}")
            return 2

        try:
            subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
        except FileNotFoundError:
            print(
                "INPUT ERROR: exiftool is required for --set-date. Install exiftool and retry.",
                file=sys.stderr,
            )
            return 2
        except subprocess.CalledProcessError as exc:
            print(f"INPUT ERROR: failed to run exiftool: {exc}")
            return 2

    if args.set_location:
        try:
            set_location = parse_set_location(args.set_location)
        except ValueError as exc:
            print(f"INPUT ERROR: {exc}")
            return 2

        try:
            subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
        except FileNotFoundError:
            print(
                "INPUT ERROR: exiftool is required for --set-location. Install exiftool and retry.",
                file=sys.stderr,
            )
            return 2
        except subprocess.CalledProcessError as exc:
            print(f"INPUT ERROR: failed to run exiftool: {exc}")
            return 2

    client_secret = Path(args.client_secret).expanduser().resolve()
    token_file = Path(args.token_file).expanduser().resolve()

    try:
        creds = get_credentials(client_secret, token_file)
    except Exception as exc:
        print(f"AUTH ERROR: {exc}")
        return 1

    upload_tokens: list[tuple[Path, str]] = []
    failed = 0

    for idx, file_path in enumerate(files, start=1):
        try:
            if set_date_dt is not None:
                updated = apply_metadata_date(file_path, set_date_dt)
                if updated:
                    log(args.debug, f"SET DATE {idx:03d}/{len(files)}: {file_path.name}")
                else:
                    log(
                        args.debug,
                        f"SET DATE SKIP {idx:03d}/{len(files)}: {file_path.name} (unsupported format)",
                    )

            if set_location is not None:
                updated_location = apply_metadata_location(file_path, set_location[0], set_location[1])
                if updated_location:
                    log(args.debug, f"SET LOCATION {idx:03d}/{len(files)}: {file_path.name}")
                else:
                    log(
                        args.debug,
                        f"SET LOCATION SKIP {idx:03d}/{len(files)}: {file_path.name} (unsupported format)",
                    )

            token = upload_bytes(creds, file_path)
            upload_tokens.append((file_path, token))
            log(args.debug, f"UPLOADED BYTES {idx:03d}/{len(files)}: {file_path.name}")
        except subprocess.CalledProcessError as exc:
            failed += 1
            stderr = (exc.stderr or "").strip()
            print(
                f"SET DATE FAILED: {file_path}\n  reason: {stderr or str(exc)}",
                file=sys.stderr,
            )
        except Exception as exc:
            failed += 1
            print(f"UPLOAD FAILED: {file_path}\n  reason: {exc}")

    created = 0
    create_failed = 0

    for token_chunk in chunked(upload_tokens, 50):
        try:
            results = batch_create_media_items(creds, token_chunk)
        except Exception as exc:
            create_failed += len(token_chunk)
            print(f"BATCH CREATE FAILED for {len(token_chunk)} item(s): {exc}")
            continue

        file_by_name = {path.name: path for path, _ in token_chunk}

        for result in results:
            status = result.get("status", {})
            message = status.get("message", "")
            item = result.get("mediaItem", {})
            filename = item.get("filename", "unknown")
            src = file_by_name.get(filename)
            label = str(src) if src else filename

            if message == "Success":
                created += 1
                log(args.debug, f"CREATED: {label}")
            else:
                create_failed += 1
                status_json = json.dumps(status, ensure_ascii=True)
                print(f"CREATE FAILED: {label} status={status_json}")

    print(
        "Done. "
        f"Found={len(files)} UploadedBytes={len(upload_tokens)} "
        f"Created={created} UploadFailed={failed} CreateFailed={create_failed}"
    )

    return 0 if (failed == 0 and create_failed == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
