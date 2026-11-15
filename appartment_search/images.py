"""Copie locale des photos, pour les garder même si l'annonce disparaît."""

import logging
import mimetypes
import sqlite3
import time
from pathlib import Path

import requests

from .scrapers.base import USER_AGENT

log = logging.getLogger(__name__)

EXT = {"image/webp": ".webp", "image/jpeg": ".jpg", "image/png": ".png", "image/avif": ".avif"}


def telecharger(conn: sqlite3.Connection, dossier: str, scrapers: dict,
                limit: int = 1000, delay: float = 0.2) -> None:
    rows = conn.execute(
        "SELECT listing_id, idx, url FROM images WHERE chemin IS NULL "
        "ORDER BY listing_id DESC, idx LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return
    base = Path(dossier)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    ok = 0
    for listing_id, idx, url in rows:
        source, source_id = listing_id.split(":", 1)
        scraper = scrapers.get(source)
        dl = scraper.image_download_url(url) if scraper else url
        try:
            r = session.get(dl, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("image %s #%d : %s", listing_id, idx, e)
            continue
        ctype = r.headers.get("content-type", "").split(";")[0]
        ext = EXT.get(ctype) or mimetypes.guess_extension(ctype) or ".img"
        rel = Path(source) / source_id / f"{idx:02d}{ext}"
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(r.content)
        conn.execute("UPDATE images SET chemin = ? WHERE listing_id = ? AND idx = ?",
                     (rel.as_posix(), listing_id, idx))
        conn.commit()
        ok += 1
        time.sleep(delay)
    log.info("images : %d téléchargées", ok)
