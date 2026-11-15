"""Isochrones « inversées » : temps de trajet de chaque point d'une grille vers
chaque destination. OTP 2.x n'a plus d'API d'isochrones, alors on interroge le
planificateur point par point (une seule fois, résultats en cache)."""

import logging
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from .trajets import MODES, prochaine_arrivee, slug, trajet

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS isochrones (
    destination  TEXT NOT NULL,
    mode         TEXT NOT NULL,
    lat          REAL NOT NULL,
    lng          REAL NOT NULL,
    pas_m        INTEGER NOT NULL,   -- taille de la cellule
    minutes      INTEGER,            -- NULL : inatteignable (eau, hors graphe...)
    calcule_le   TEXT NOT NULL,
    PRIMARY KEY (destination, mode, lat, lng)
);
"""


def grille(bbox: list[float], pas_m: int) -> list[tuple[float, float]]:
    """bbox = [ouest, sud, est, nord]. Points espacés de pas_m mètres."""
    ouest, sud, est, nord = bbox
    dlat = pas_m / 111_320
    dlng = pas_m / (111_320 * math.cos(math.radians((sud + nord) / 2)))
    pts = []
    lat = sud + dlat / 2
    while lat < nord:
        lng = ouest + dlng / 2
        while lng < est:
            pts.append((round(lat, 6), round(lng, 6)))
            lng += dlng
        lat += dlat
    return pts


def calculer(conn: sqlite3.Connection, cfg: dict, destinations: list[dict], otp_url: str,
             arrivee_hhmm: str) -> None:
    conn.executescript(SCHEMA)
    bbox = cfg.get("bbox", [-71.55, 46.70, -71.05, 47.00])
    pas_m = cfg.get("pas_m", 500)
    modes = cfg.get("modes", ["bus", "velo"])
    workers = cfg.get("paralleles", 4)
    arrivee = prochaine_arrivee(arrivee_hhmm)
    pts = grille(bbox, pas_m)

    try:
        requests.get(otp_url.rsplit("/otp", 1)[0], timeout=5)
    except requests.RequestException:
        log.error("OTP injoignable (%s)", otp_url)
        return

    for d in destinations:
        for mode in modes:
            if mode not in MODES:
                log.warning("mode inconnu : %s", mode)
                continue
            dest = slug(d["nom"])
            faits = {
                (lat, lng) for lat, lng in conn.execute(
                    "SELECT lat, lng FROM isochrones WHERE destination = ? AND mode = ? AND pas_m = ?",
                    (dest, mode, pas_m))
            }
            todo = [p for p in pts if p not in faits]
            log.info("isochrone %s / %s : %d points à calculer (%d en cache)",
                     d["nom"], mode, len(todo), len(faits))
            if not todo:
                continue

            def un_point(p):
                res = trajet(otp_url, p, (d["lat"], d["lng"]), mode, arrivee)
                return p, res and res["minutes"]

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with ThreadPoolExecutor(workers) as pool:
                futures = [pool.submit(un_point, p) for p in todo]
                for i, f in enumerate(as_completed(futures), 1):
                    try:
                        (lat, lng), minutes = f.result()
                    except Exception as e:
                        log.warning("point ignoré : %s", e)
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO isochrones VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (dest, mode, lat, lng, pas_m, minutes, now),
                    )
                    if i % 200 == 0:
                        conn.commit()
                        log.info("  %d / %d", i, len(todo))
            conn.commit()
