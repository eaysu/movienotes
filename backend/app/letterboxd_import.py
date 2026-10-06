"""Parse a member's own Letterboxd export without contacting Letterboxd."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import urlparse

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_CSV_BYTES = 24 * 1024 * 1024
MAX_ROWS = 50_000
_SHORT_ID = re.compile(r"^[A-Za-z0-9]{2,24}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,159}$")


class InvalidLetterboxdExport(ValueError):
    pass


@dataclass
class LetterboxdExport:
    watched: list[dict]
    watchlist: list[dict]
    username: str | None
    watchlist_present: bool


def _film_slug(uri: str) -> str | None:
    parsed = urlparse(uri.strip())
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https":
        return None
    if host == "boxd.it" and len(parts) == 1 and _SHORT_ID.fullmatch(parts[0]):
        # Hex retains the short link's case while keeping the DB slug safe.
        return "boxd-" + parts[0].encode("ascii").hex()
    if host in {"letterboxd.com", "www.letterboxd.com"} and len(parts) == 2 and parts[0] == "film" and _SLUG.fullmatch(parts[1]):
        return parts[1]
    return None


def _read_csv(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    info = archive.getinfo(name)
    if info.file_size > MAX_CSV_BYTES:
        raise InvalidLetterboxdExport("CSV dosyası çok büyük.")
    try:
        raw = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise InvalidLetterboxdExport("ZIP dosyası okunamadı.") from exc
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        if not reader.fieldnames:
            raise InvalidLetterboxdExport("CSV dosyası boş.")
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise InvalidLetterboxdExport("CSV çok fazla satır içeriyor.")
            rows.append(row)
        return rows
    except UnicodeDecodeError as exc:
        raise InvalidLetterboxdExport("CSV UTF-8 değil.") from exc


def parse_letterboxd_export(data: bytes) -> LetterboxdExport:
    if len(data) > MAX_UPLOAD_BYTES or not zipfile.is_zipfile(io.BytesIO(data)):
        raise InvalidLetterboxdExport("Geçerli bir Letterboxd ZIP dışa aktarımı yükle.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if "watched.csv" not in names and "ratings.csv" not in names:
            raise InvalidLetterboxdExport("ZIP içinde watched.csv veya ratings.csv bulunamadı.")
        selected = {name: _read_csv(archive, name) for name in ("profile.csv", "watched.csv", "ratings.csv", "diary.csv", "watchlist.csv") if name in names}

    username = None
    if selected.get("profile.csv"):
        username = (selected["profile.csv"][0].get("Username") or "").strip().lower() or None

    films: dict[str, dict] = {}
    by_name_year: dict[tuple[str, int | None], set[str]] = {}
    # watched.csv provides the film short URL; diary.csv's URI points to the
    # diary entry, so attach it by title/year only when the match is unique.
    for name in ("watched.csv", "ratings.csv"):
        for row in selected.get(name, []):
            title = (row.get("Name") or "").strip()
            slug = _film_slug(row.get("Letterboxd URI") or "")
            if not title or not slug:
                continue
            year_raw = (row.get("Year") or "").strip()
            year = int(year_raw) if year_raw.isdigit() and len(year_raw) == 4 else None
            item = films.setdefault(slug, {"slug": slug, "title": title, "release_year": year, "rating_observed": False, "date": ""})
            if name == "watched.csv" or not item["date"]:
                item["date"] = (row.get("Date") or "").strip()
            by_name_year.setdefault((title.casefold(), year), set()).add(slug)
            if name == "ratings.csv":
                try:
                    rating = float(row.get("Rating") or "")
                except ValueError:
                    continue
                if 0.5 <= rating <= 5 and rating * 2 == int(rating * 2):
                    item.update(user_rating=rating, rating_observed=True)

    for row in selected.get("diary.csv", []):
        title = (row.get("Name") or "").strip()
        year_raw = (row.get("Year") or "").strip()
        year = int(year_raw) if year_raw.isdigit() and len(year_raw) == 4 else None
        matches = by_name_year.get((title.casefold(), year), set())
        if title and len(matches) == 1:
            item = films[next(iter(matches))]
            item["date"] = (row.get("Watched Date") or row.get("Date") or item["date"]).strip()
            try:
                rating = float(row.get("Rating") or "")
            except ValueError:
                continue
            if not item["rating_observed"] and 0.5 <= rating <= 5 and rating * 2 == int(rating * 2):
                item.update(user_rating=rating, rating_observed=True)

    if not films:
        raise InvalidLetterboxdExport("Dışa aktarımda geçerli film bulunamadı.")
    watched = sorted(films.values(), key=lambda item: item["date"], reverse=True)
    for rank, item in enumerate(watched):
        item.pop("date")
        item["watched_rank"] = rank

    watchlist = []
    seen = set()
    for row in selected.get("watchlist.csv", []):
        slug = _film_slug(row.get("Letterboxd URI") or "")
        title = (row.get("Name") or "").strip()
        if not slug or not title or slug in seen:
            continue
        seen.add(slug)
        year_raw = (row.get("Year") or "").strip()
        watchlist.append({"slug": slug, "title": title, "year": int(year_raw) if year_raw.isdigit() and len(year_raw) == 4 else None})
    return LetterboxdExport(watched, watchlist, username, "watchlist.csv" in selected)
