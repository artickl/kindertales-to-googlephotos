# KinderTales Photo Backup to Google Photos

This project helps parents export daily photos from KinderTales which is used by CEFA daycare and upload them
to Google Photos with corrected metadata.

At CEFA daycare, KinderTales provides daily photo updates for each child. Two practical challenges appear when you want to
build a clean Google Photos timeline:

1. If you download old days later, files may end up with the wrong date unless metadata is corrected.
2. Downloaded files usually have no location metadata, which makes Google Photos location search less useful.

This repository includes scripts to solve that.

## What These Scripts Do

1. `download_activities.py`
   - Downloads all full-resolution activity images for one date from KinderTales.
   - Saves them under `downloads/YYYY-MM-DD/`.
   - Fixing EXIF date to match downloaded date and optional can fix EXIF GPS location.

2. `upload_google_photos.py`
   - Uploads one image or a folder of images to Google Photos.
   - Can set date/location metadata before upload if needed.

3. `download-upload-batch.sh`
   - Combines download + upload.
   - Default behavior: process yesterday only.
   - If `--month` and/or `--year` are provided without `--day`, it processes the full month.
   - If `--day` is provided, it processes only that day.

## Requirements

- Linux/macOS shell environment
- Python 3.10+
- `exiftool`
- Python packages:
  - `requests`
  - `google-auth`
  - `google-auth-oauthlib`

Install dependencies:

```bash
python3 -m pip install requests google-auth google-auth-oauthlib
```

Install exiftool (Ubuntu/Debian):

```bash
sudo apt-get update && sudo apt-get install -y libimage-exiftool-perl
```

## Secrets and Local Files

These files are intentionally gitignored:

- `cookie.txt`
- `google_client_secret.json`
- `google_photos_token.json`
- `downloads/*`

### 1) Create `cookie.txt`

`cookie.txt` must contain the raw Cookie header value for `app.kindertales.com`.

Format example:

```text
name1=value1; name2=value2; name3=value3
```

Ways to get it:

1. Chrome DevTools
   - Open KinderTales page while logged in.
   - Open Network tab, reload page, click the main document request.
   - Copy the `Cookie` request header value (without the `Cookie:` prefix).
   - Save to `cookie.txt`.

2. From an existing curl command
   - If your curl command has `-b '...'`, copy that content into `cookie.txt`.

Protect it:

```bash
chmod 600 cookie.txt
```

Notes:

- Session cookies expire. Refresh `cookie.txt` when auth starts failing.
- Treat this file like a password.

### 2) Create `google_client_secret.json`

1. In Google Cloud Console, enable Google Photos Library API.
2. Create OAuth client credentials for a Desktop app.
3. Download JSON credentials and save as `google_client_secret.json` in this project root.

### 3) Generate `google_photos_token.json`

Run `upload_google_photos.py` once. It opens browser OAuth flow and saves token locally.

Example:

```bash
./upload_google_photos.py --file /path/to/some-image.jpg
```

After successful login, `google_photos_token.json` will be created.

## Script Usage

## 1) Download images for one date

Basic:

```bash
./download_activities.py --cid <child_id> --cookie-file cookie.txt --date 2026-06-01
```

With EXIF date and location:

```bash
./download_activities.py \
  --cid <child_id> \
  --cookie-file cookie.txt \
  --date 2026-06-01 \
  --set-date-exif \
  --set-location 49.2827,-123.1207
```

Notes:

- Date format supports `YYYY-MM-DD` or `MM/DD/YYYY`.
- Output goes to `downloads/2026-06-01/`.

## 2) Upload to Google Photos

Single file:

```bash
./upload_google_photos.py --file downloads/2026-06-01/photo.jpg
```

Folder upload:

```bash
./upload_google_photos.py --folder downloads/2026-06-01
```

Folder upload with metadata update before upload:

```bash
./upload_google_photos.py \
  --folder downloads/2026-06-01 \
  --set-date 2026-06-01 \
  --set-location 49.2827,-123.1207
```

Dry run:

```bash
./upload_google_photos.py --folder downloads/2026-06-01 --dry-run
```

## 3) Combined batch script

Default (yesterday only):

```bash
./download-upload-batch.sh --cid <child_id>
```

Default with location:

```bash
./download-upload-batch.sh --cid <child_id> --location 49.2827,-123.1207
```

Single specific day:

```bash
./download-upload-batch.sh --cid <child_id> --year 2026 --month 06 --day 01
```

Full month:

```bash
./download-upload-batch.sh --cid <child_id> --year 2026 --month 06
```

Full month with location:

```bash
./download-upload-batch.sh --cid <child_id> --year 2026 --month 06 --location 49.2827,-123.1207
```

## Recommended Workflow

1. Start with one day in dry-run/small mode to validate auth and metadata.
2. Confirm EXIF fields with `exiftool`.
3. Run month batch only after single-day success.
4. Refresh `cookie.txt` if KinderTales fetch fails.

Check EXIF quickly:

```bash
exiftool downloads/2026-06-01/*.jpg | grep -E "Date/Time Original|GPS Latitude|GPS Longitude"
```

## Troubleshooting

- `CONFIG ERROR: No cookie found`
  - Provide `--cookie-file` or set `KINDERTALES_COOKIE`.

- `FETCH ERROR` from KinderTales
  - Cookie is expired/invalid or CID/date has no data.

- `No gallery links found`
  - No photos for that day, not authenticated, or page structure changed.

- `AUTH ERROR` in Google uploader
  - Check `google_client_secret.json` and OAuth setup.

- `exiftool is required`
  - Install exiftool package.

## Privacy Notes

- These scripts process child photos and account sessions.
- Keep credential files private and local.
- Do not commit `cookie.txt` / OAuth files to git.
