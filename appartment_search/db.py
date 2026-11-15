import json
import sqlite3
from datetime import datetime, timezone

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    titre         TEXT,
    prix          INTEGER,
    taille        TEXT,
    adresse       TEXT,
    disponible    TEXT,
    url           TEXT NOT NULL,
    premiere_vue  TEXT NOT NULL,
    derniere_vue  TEXT NOT NULL,
    actif         INTEGER DEFAULT 1,
    raw           TEXT
);

CREATE TABLE IF NOT EXISTS geo (
    adresse   TEXT PRIMARY KEY,
    lat       REAL,
    lng       REAL,
    data      TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def known_ids(conn: sqlite3.Connection, source: str) -> set[str]:
    rows = conn.execute("SELECT id FROM listings WHERE source = ?", (source,))
    return {r[0] for r in rows}


def upsert(conn: sqlite3.Connection, listings: list[Listing]) -> list[Listing]:
    """Insère les nouvelles annonces, met à jour derniere_vue des autres.
    Retourne seulement les nouvelles."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = []
    for l in listings:
        cur = conn.execute(
            "UPDATE listings SET derniere_vue = ?, prix = ?, actif = 1 WHERE id = ?",
            (now, l.prix, l.id),
        )
        if cur.rowcount:
            continue
        conn.execute(
            """INSERT INTO listings
               (id, source, titre, prix, taille, adresse, disponible, url,
                premiere_vue, derniere_vue, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (l.id, l.source, l.titre, l.prix, l.taille, l.adresse, l.disponible,
             l.url, now, now, json.dumps(l.raw, ensure_ascii=False)),
        )
        if l.adresse and l.lat is not None and l.lng is not None:
            conn.execute(
                "INSERT OR IGNORE INTO geo (adresse, lat, lng) VALUES (?, ?, ?)",
                (l.adresse, l.lat, l.lng),
            )
        new.append(l)
    conn.commit()
    return new
