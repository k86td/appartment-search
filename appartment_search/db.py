import json
import sqlite3
from datetime import datetime, timezone

from .models import Contact, Details, Listing

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

-- Page complète de chaque annonce, récupérée une seule fois (cache).
CREATE TABLE IF NOT EXISTS details (
    listing_id   TEXT PRIMARY KEY REFERENCES listings(id),
    fetched_at   TEXT NOT NULL,
    description  TEXT,
    images       TEXT,            -- JSON : liste d'URLs
    raw          TEXT             -- JSON : tout ce que la page fournit
);

-- Attributs en format long : (annonce, attribut) -> valeur. Facile à facetter.
CREATE TABLE IF NOT EXISTS attributs (
    listing_id    TEXT NOT NULL REFERENCES listings(id),
    nom           TEXT NOT NULL,  -- nom canonique : furnished, petsallowed...
    libelle       TEXT,           -- "Furnished", "Pet Friendly"...
    valeur        TEXT,           -- "Yes", "No", "611"...
    valeur_canon  TEXT,           -- "1", "0", "611"...
    PRIMARY KEY (listing_id, nom)
);

-- Coordonnées de l'annonceur, telles que la page les donne.
CREATE TABLE IF NOT EXISTS contacts (
    listing_id         TEXT PRIMARY KEY REFERENCES listings(id),
    nom                TEXT,
    type               TEXT,
    telephones         TEXT,   -- trouvés dans la description, séparés par ", "
    courriels          TEXT,
    telephone_partiel  TEXT,   -- numéro masqué fourni par le site
    site_web           TEXT,
    profil_url         TEXT
);

-- Photos : URL d'origine + copie locale (chemin relatif au dossier d'images).
CREATE TABLE IF NOT EXISTS images (
    listing_id  TEXT NOT NULL REFERENCES listings(id),
    idx         INTEGER NOT NULL,
    url         TEXT NOT NULL,
    chemin      TEXT,          -- NULL tant que pas téléchargée
    PRIMARY KEY (listing_id, idx)
);

-- Vue pratique pour Datasette : annonce + description + attributs courants.
CREATE VIEW IF NOT EXISTS annonces AS
SELECT
    l.id, l.source, l.titre, l.prix, l.taille, l.adresse, l.disponible, l.url,
    l.premiere_vue, l.actif,
    d.description,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'furnished')          AS meuble,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'petsallowed')        AS animaux,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'heat')               AS chauffage_inclus,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'hydro')              AS hydro_inclus,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'numberparkingspots') AS stationnements,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'laundryinunit')      AS laveuse_secheuse,
    (SELECT valeur FROM attributs a WHERE a.listing_id = l.id AND a.nom = 'areainfeet')         AS pi2,
    g.lat AS latitude, g.lng AS longitude
FROM listings l
LEFT JOIN details d ON d.listing_id = l.id
LEFT JOIN geo g ON g.adresse = l.adresse;
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


def pending_details(conn: sqlite3.Connection, source: str, limit: int) -> list[tuple[str, str, str]]:
    """Annonces actives sans détails en cache : (id, source_id, url).
    Inclut les nouvelles et celles dont un essai précédent a échoué."""
    rows = conn.execute(
        """SELECT l.id, l.url FROM listings l
           WHERE l.source = ? AND l.actif = 1
             AND NOT EXISTS (SELECT 1 FROM details d WHERE d.listing_id = l.id)
           ORDER BY l.premiere_vue DESC LIMIT ?""",
        (source, limit),
    )
    return [(id_, id_.split(":", 1)[1], url) for id_, url in rows]


def save_details(conn: sqlite3.Connection, listing_id: str, d: Details) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO details VALUES (?, ?, ?, ?, ?)",
        (listing_id, now, d.description, json.dumps(d.images),
         json.dumps(d.raw, ensure_ascii=False)),
    )
    if d.contact:
        save_contact(conn, listing_id, d.contact)
    conn.executemany(
        "INSERT OR IGNORE INTO images (listing_id, idx, url) VALUES (?, ?, ?)",
        [(listing_id, i, u) for i, u in enumerate(d.images)],
    )
    conn.execute("DELETE FROM attributs WHERE listing_id = ?", (listing_id,))
    conn.executemany(
        "INSERT INTO attributs VALUES (?, ?, ?, ?, ?)",
        [(listing_id, nom, lib, val, canon) for nom, (lib, val, canon) in d.attributs.items()],
    )
    conn.commit()


def mark_inactive(conn: sqlite3.Connection, listing_id: str) -> None:
    conn.execute("UPDATE listings SET actif = 0 WHERE id = ?", (listing_id,))
    conn.commit()


def save_contact(conn: sqlite3.Connection, listing_id: str, c: Contact) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO contacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (listing_id, c.nom, c.type, ", ".join(c.telephones) or None,
         ", ".join(c.courriels) or None, c.telephone_partiel, c.site_web, c.profil_url),
    )


def backfill(conn: sqlite3.Connection, scrapers: dict) -> None:
    """Remplit contacts et images pour les annonces dont les détails ont été
    récupérés avant l'ajout de ces tables (à partir de details.raw), et nettoie
    les descriptions HTML enregistrées avant le nettoyage."""
    from .scrapers.base import texte_propre
    for listing_id, desc in conn.execute(
        "SELECT listing_id, description FROM details WHERE description LIKE '%<%' OR description LIKE '%&%;%'"
    ).fetchall():
        conn.execute("UPDATE details SET description = ? WHERE listing_id = ?",
                     (texte_propre(desc), listing_id))

    rows = conn.execute(
        """SELECT d.listing_id, d.images, d.raw FROM details d
           WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.listing_id = d.listing_id)"""
    ).fetchall()
    for listing_id, images, raw in rows:
        scraper = scrapers.get(listing_id.split(":", 1)[0])
        contact = scraper.contact_from_raw(json.loads(raw or "{}")) if scraper else None
        save_contact(conn, listing_id, contact or Contact())
        conn.executemany(
            "INSERT OR IGNORE INTO images (listing_id, idx, url) VALUES (?, ?, ?)",
            [(listing_id, i, u) for i, u in enumerate(json.loads(images or "[]"))],
        )
    conn.commit()


def mark_missing_inactive(conn: sqlite3.Connection, source: str, seen: set[str]) -> None:
    """Pour les sources qui renvoient tout leur catalogue : absent = retiré."""
    actifs = {r[0] for r in conn.execute(
        "SELECT id FROM listings WHERE source = ? AND actif = 1", (source,))}
    for listing_id in actifs - seen:
        conn.execute("UPDATE listings SET actif = 0 WHERE id = ?", (listing_id,))
    conn.commit()
