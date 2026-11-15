"""Déduplication : regroupe en « offres » les annonces qui décrivent le même logement
sur plusieurs sites (ex. GestiPro + Kijiji).

Deux annonces sont la même offre si :
  - même immeuble : même numéro civique, et GPS à < 40 m (ou même rue si pas de GPS) ;
  - même taille (4½…) et prix à ±3 % ;
  - numéros d'appartement identiques, s'ils sont connus des deux côtés ;
  - sites différents (jamais deux unités d'un même gestionnaire), sauf une annonce
    Kijiji republiée (même texte) ;
  - sans ambiguïté : si une annonce correspond à plusieurs unités d'un même site,
    on ne fusionne pas.

Résultat : table `offres` (listing_id -> offre_id = id de l'annonce principale).
Chaque annonce active a une ligne, seule ou non. Recalculée à chaque passage."""

import logging
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS offres (
    listing_id  TEXT PRIMARY KEY,
    offre_id    TEXT NOT NULL,       -- id de l'annonce principale du groupe
    principale  INTEGER NOT NULL     -- 1 pour l'annonce affichée
);
CREATE INDEX IF NOT EXISTS offres_offre ON offres(offre_id);
"""

# Sites des gestionnaires : leurs infos priment sur celles des sites d'annonces.
PRIORITE = {"msi": 0, "logisco": 0, "gestipro": 0, "kijiji": 2}
DIST_MAX_M = 40
TOLERANCE_PRIX = 0.03


def _norm(t: str | None) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[’'`]", " ", t)
    for a, b in [(r"\bboulevard\b", "boul"), (r"\bavenue\b", "av"), (r"\bchemin\b", "ch"),
                 (r"\bsaint(e)?\b", "st"), (r"\bmont\b", "mt"), (r"\bdu\b|\bde la\b|\bdes\b|\bde\b|\bd\b", " ")]:
        t = re.sub(a, b, t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


_UNITE = re.compile(r"(?:#|\bapp(?:artement)?\.?|\bapt\.?|\bunite|\blogement|\bsuite)\s*([0-9]{1,4}[a-z]?)\b")


def adresse_cle(adresse: str | None, titre: str | None) -> dict:
    """Numéro civique (ou plage), rue normalisée, numéro d'appartement."""
    a, t = _norm(adresse), _norm(titre)
    unite = None
    m = _UNITE.search(f"{t} {a}".replace("appartement", "app"))
    if m:
        unite = m.group(1)
    civ = None
    # « 318-3900 boul ... » = app. 318 au 3900 ; « 1408-1418 ... » = plage de numéros
    m = re.match(r"(\d{1,5})\s+(\d{1,5})\s+(.*)", a) or re.match(r"(\d{1,5})-(\d{1,5})\s+(.*)", adresse or "")
    rue = a
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        if y - x >= 50 or x > y:           # unité-civique
            unite = unite or str(x)
            civ = (y, y)
        else:                              # plage
            civ = (x, y)
        rue = _norm(m.group(3))
    else:
        m = re.match(r"(\d{1,5})\s+(.*)", a)
        if m:
            civ = (int(m.group(1)), int(m.group(1)))
            rue = m.group(2)
    rue = " ".join(w for w in rue.split()[:3] if not w.isdigit())   # « av chapdelaine quebec »
    return {"civ": civ, "rue": rue, "unite": unite}


def _dist_m(a, b) -> float:
    if None in (a["lat"], a["lng"], b["lat"], b["lng"]):
        return math.inf
    dx = (a["lng"] - b["lng"]) * 111_320 * math.cos(math.radians(a["lat"]))
    dy = (a["lat"] - b["lat"]) * 110_574
    return math.hypot(dx, dy)


def meme_offre(a: dict, b: dict) -> bool:
    if a["taille"] is None or a["taille"] != b["taille"]:
        return False
    if not a["prix"] or not b["prix"] or abs(a["prix"] - b["prix"]) > TOLERANCE_PRIX * max(a["prix"], b["prix"]):
        return False
    ka, kb = a["cle"], b["cle"]
    if ka["unite"] and kb["unite"] and ka["unite"] != kb["unite"]:
        return False
    if a["source"] == b["source"]:
        # Seule exception : annonce Kijiji republiée (même texte, même immeuble).
        return (a["source"] == "kijiji" and a["texte"] and a["texte"] == b["texte"]
                and _dist_m(a, b) < DIST_MAX_M)
    if not ka["civ"] or not kb["civ"]:
        return False
    if ka["civ"][0] > kb["civ"][1] or kb["civ"][0] > ka["civ"][1]:   # numéros civiques disjoints
        return False
    d = _dist_m(a, b)
    return d < DIST_MAX_M if d != math.inf else (ka["rue"] and ka["rue"] == kb["rue"])


def calculer(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    rows = conn.execute("""
        SELECT l.id, l.source, l.titre, l.adresse, l.prix, l.taille, g.lat, g.lng,
               substr(d.description, 1, 300),
               (SELECT count(*) FROM images i WHERE i.listing_id = l.id)
        FROM listings l
        LEFT JOIN geo g ON g.adresse = l.adresse
        LEFT JOIN details d ON d.listing_id = l.id
        WHERE l.actif = 1""").fetchall()
    ann = [{"id": r[0], "source": r[1], "titre": r[2], "adresse": r[3], "prix": r[4], "taille": r[5],
            "lat": r[6], "lng": r[7], "texte": _norm(r[8])[:200] or None, "photos": r[9],
            "cle": adresse_cle(r[3], r[2])} for r in rows]

    # Candidats par cellule GPS (~110 m) ou par numéro civique + rue, pour éviter le n².
    seaux = defaultdict(list)
    for a in ann:
        if a["lat"] is not None:
            seaux[("g", round(a["lat"], 3), round(a["lng"], 3))].append(a)
        if a["cle"]["civ"]:
            seaux[("c", a["cle"]["civ"][0], a["cle"]["rue"])].append(a)

    voisins = defaultdict(set)
    for a in ann:
        cands = []
        if a["lat"] is not None:
            for dla in (-1, 0, 1):
                for dln in (-1, 0, 1):
                    cands += seaux.get(("g", round(a["lat"] + dla * 0.001, 3), round(a["lng"] + dln * 0.001, 3)), [])
        if a["cle"]["civ"]:
            cands += seaux.get(("c", a["cle"]["civ"][0], a["cle"]["rue"]), [])
        for b in {c["id"]: c for c in cands}.values():
            if b["id"] != a["id"] and meme_offre(a, b):
                voisins[a["id"]].add(b["id"])

    # Ambiguïté : plusieurs correspondances dans un même site -> pas de fusion avec ce site.
    par_id = {a["id"]: a for a in ann}
    liens = set()
    for aid, vs in voisins.items():
        par_source = defaultdict(list)
        for v in vs:
            par_source[par_id[v]["source"]].append(v)
        for src, ids in par_source.items():
            if len(ids) == 1 and (src != par_id[aid]["source"] or src == "kijiji"):
                b = ids[0]
                # Lien retenu seulement s'il est non ambigu des deux côtés.
                inverse = [v for v in voisins[b] if par_id[v]["source"] == par_id[aid]["source"]]
                if len(inverse) == 1:
                    liens.add(tuple(sorted((aid, b))))

    # Union-find
    parent = {a["id"]: a["id"] for a in ann}
    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in liens:
        parent[racine(a)] = racine(b)
    groupes = defaultdict(list)
    for a in ann:
        groupes[racine(a["id"])].append(par_id[a["id"]])

    # Annonce principale : site du gestionnaire d'abord, puis la plus complète (photos, texte).
    conn.execute("DELETE FROM offres")
    multi = 0
    for membres in groupes.values():
        p = min(membres, key=lambda m: (PRIORITE.get(m["source"], 1), -m["photos"], -(m["texte"] is not None), m["id"]))
        multi += len(membres) > 1
        conn.executemany("INSERT INTO offres VALUES (?, ?, ?)",
                         [(m["id"], p["id"], int(m["id"] == p["id"])) for m in membres])
    # Annonces inactives : chacune sa propre offre (les aimées restent visibles).
    conn.execute("""INSERT OR IGNORE INTO offres
                    SELECT id, id, 1 FROM listings WHERE actif = 0""")
    conn.commit()
    log.info("déduplication : %d annonces actives -> %d offres (%d regroupements)",
             len(ann), len(groupes), multi)
