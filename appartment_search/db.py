import json
import sqlite3
from datetime import datetime, timezone

from .models import Caracteristiques, Contact, Details, Listing

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

-- Tes marques depuis /carte : 'aime' ou 'cache'.
CREATE TABLE IF NOT EXISTS marques (
    listing_id  TEXT PRIMARY KEY,
    statut      TEXT NOT NULL CHECK (statut IN ('aime', 'cache')),
    le          TEXT NOT NULL
);

-- Caractéristiques au format STANDARD (models.Caracteristiques), communes à tous les sites.
-- NULL = inconnu. Booléens en 0/1.
CREATE TABLE IF NOT EXISTS caracteristiques (
    listing_id           TEXT PRIMARY KEY REFERENCES listings(id),
    meuble               TEXT,     -- oui | non | semi
    animaux              TEXT,     -- oui | non | limite
    chauffage_inclus     INTEGER,
    electricite_incluse  INTEGER,
    eau_chaude_incluse   INTEGER,
    internet_inclus      INTEGER,
    stationnements       INTEGER,
    laveuse_secheuse     TEXT,     -- appareils | entrees | immeuble | non
    lave_vaisselle       INTEGER,
    climatisation        INTEGER,
    ascenseur            INTEGER,
    balcon               INTEGER,
    rangement            INTEGER,
    gym                  INTEGER,
    piscine              INTEGER,
    superficie_pi2       INTEGER,
    etage                INTEGER,
    chambres             REAL,
    salles_de_bain       REAL,
    type_logement        TEXT,
    bail                 TEXT,
    fumeur               INTEGER
);

-- Vue pratique : annonce + description + caractéristiques standard + position.
-- Recréée à chaque connexion pour suivre les changements de schéma.
DROP VIEW IF EXISTS annonces;
CREATE VIEW annonces AS
SELECT
    l.id, l.source, l.titre, l.prix, l.taille, l.adresse, l.disponible, l.url,
    l.premiere_vue, l.actif,
    d.description,
    c.meuble, c.animaux, c.chauffage_inclus, c.electricite_incluse, c.eau_chaude_incluse,
    c.internet_inclus, c.stationnements, c.laveuse_secheuse, c.lave_vaisselle, c.climatisation,
    c.ascenseur, c.balcon, c.rangement, c.gym, c.piscine, c.superficie_pi2, c.etage,
    c.chambres, c.salles_de_bain, c.type_logement, c.bail, c.fumeur,
    g.lat AS latitude, g.lng AS longitude
FROM listings l
LEFT JOIN details d ON d.listing_id = l.id
LEFT JOIN caracteristiques c ON c.listing_id = l.id
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
    save_caracteristiques(conn, listing_id, d.caracteristiques)
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


def save_caracteristiques(conn: sqlite3.Connection, listing_id: str, c: Caracteristiques) -> None:
    champs = Caracteristiques.CHAMPS
    vals = [getattr(c, f) for f in champs]
    vals = [int(v) if isinstance(v, bool) else v for v in vals]
    conn.execute(
        f"INSERT OR REPLACE INTO caracteristiques (listing_id, {', '.join(champs)}) "
        f"VALUES (?, {', '.join('?' * len(champs))})",
        [listing_id, *vals],
    )


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

    # Annonces en cache sans contact ou sans caractéristiques standard : on les reconstruit
    # depuis le JSON brut, sans refaire de requête.
    rows = conn.execute(
        """SELECT d.listing_id, d.images, d.raw FROM details d
           WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.listing_id = d.listing_id)
              OR NOT EXISTS (SELECT 1 FROM caracteristiques k WHERE k.listing_id = d.listing_id)"""
    ).fetchall()
    for listing_id, images, raw in rows:
        scraper = scrapers.get(listing_id.split(":", 1)[0])
        det = scraper.details_from_raw(json.loads(raw or "{}")) if scraper else None
        if not conn.execute("SELECT 1 FROM contacts WHERE listing_id = ?", (listing_id,)).fetchone():
            save_contact(conn, listing_id, (det and det.contact) or Contact())
        save_caracteristiques(conn, listing_id, det.caracteristiques if det else Caracteristiques())
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
