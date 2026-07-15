#!/bin/bash

set -u

usage() {
    echo "Usage: $0 --cid <child_id> [--location <lat,lon>] [--month MM] [--year YYYY] [--day DD] [--cookie-file path]"
    echo "Defaults: yesterday only. If --month/--year are provided without --day, full month is processed."
    echo "cookie-file defaults to cookie.txt"
}

DATE_INFO="$(date -d "-1 day" +%Y-%m-%d)"
DEFAULT_YEAR="${DATE_INFO%%-*}"
DEFAULT_MONTH="${DATE_INFO#*-}"
DEFAULT_MONTH="${DEFAULT_MONTH%-*}"
DEFAULT_DAY="${DATE_INFO##*-}"

CID=""
LOCATION=""
MONTH="$DEFAULT_MONTH"
YEAR="$DEFAULT_YEAR"
DAY=""
COOKIE_FILE="cookie.txt"
MONTH_PROVIDED=0
YEAR_PROVIDED=0
DAY_PROVIDED=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cid)
            CID="$2"
            shift 2
            ;;
        --set-location)
            LOCATION="$2"
            shift 2
            ;;
        --month)
            MONTH="$2"
            MONTH_PROVIDED=1
            shift 2
            ;;
        --year)
            YEAR="$2"
            YEAR_PROVIDED=1
            shift 2
            ;;
        --day)
            DAY="$2"
            DAY_PROVIDED=1
            shift 2
            ;;
        --cookie-file)
            COOKIE_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$CID" ]]; then
    echo "Error: --cid is required"
    usage
    exit 2
fi

if ! [[ "$MONTH" =~ ^(0[1-9]|1[0-2])$ ]]; then
    echo "Error: --month must be MM (01-12)"
    exit 2
fi

if ! [[ "$YEAR" =~ ^[0-9]{4}$ ]]; then
    echo "Error: --year must be YYYY"
    exit 2
fi

DAYS_IN_MONTH="$(date -d "$YEAR-$MONTH-01 +1 month -1 day" +%d)"

# Default mode: yesterday only.
# If month/year were explicitly provided and day was not, run full month.
if [[ "$DAY_PROVIDED" -eq 0 ]]; then
    if [[ "$MONTH_PROVIDED" -eq 0 && "$YEAR_PROVIDED" -eq 0 ]]; then
        DAY="$DEFAULT_DAY"
        DAY_PROVIDED=1
    fi
fi

if [[ "$DAY_PROVIDED" -eq 1 ]]; then
    if ! [[ "$DAY" =~ ^(0[1-9]|[12][0-9]|3[01])$ ]]; then
        echo "Error: --day must be DD (01-31)"
        exit 2
    fi

    if (( 10#$DAY > 10#$DAYS_IN_MONTH )); then
        echo "Error: --day $DAY is invalid for $YEAR-$MONTH"
        exit 2
    fi

    START_DAY=$((10#$DAY))
    END_DAY=$((10#$DAY))
else
    START_DAY=1
    END_DAY=$((10#$DAYS_IN_MONTH))
fi

for i in $(seq "$START_DAY" "$END_DAY"); do
    printf -v DAY "%02d" "$i"
    echo -e "\n============================================================="
    echo -e "DATE $MONTH/$DAY/$YEAR:"
    echo "Downloading activities for $MONTH/$DAY/$YEAR"

    download_cmd=(
        ./download_activities.py
        --cid "$CID"
        --cookie-file "$COOKIE_FILE"
        --date "$MONTH/$DAY/$YEAR"
        --set-date-exif
    )
    upload_cmd=(
        ./upload_google_photos.py
        --set-date "$YEAR-$MONTH-$DAY"
        --folder "downloads/$YEAR-$MONTH-$DAY/"
    )

    if [[ -n "$LOCATION" ]]; then
        download_cmd+=(--set-location "$LOCATION")
        upload_cmd+=(--set-location "$LOCATION")
    fi

    "${download_cmd[@]}"
    download_exit_code=$?

    if [[ "$download_exit_code" -eq 1 ]]; then
        echo "ERROR: No images for $MONTH/$DAY/$YEAR."
        echo "Skipping upload."
        continue
    fi

    if [[ "$download_exit_code" -eq 2 ]]; then
        echo "ERROR: Was not able to access $MONTH/$DAY/$YEAR."
        echo "Stopping batch run."
        exit "$download_exit_code"
    fi

    echo "Uploading activities for $MONTH/$DAY/$YEAR"
    if ! "${upload_cmd[@]}"; then
        echo "Upload failed for $MONTH/$DAY/$YEAR. Continuing to next day."
        echo -e "=============================================================\n"
        continue
    fi

    echo -e "=============================================================\n"
done