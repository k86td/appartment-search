"""Temps de trajet de chaque adresse vers les destinations de config.toml,
calculés par OpenTripPlanner (API GraphQL GTFS)."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re
import sqlite3
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

MODES = {
    # nom -> modes OTP. None = défaut OTP (marche + transport en commun).
    "bus": None,
    "velo": {"directOnly": True, "direct": ["BICYCLE"]},
    "auto": {"directOnly": True, "direct": ["CAR"]},
}

QUERY = """
query($o: PlanLabeledLocationInput!, $d: PlanLabeledLocationInput!,
      $dt: PlanDateTimeInput, $m: PlanModesInput) {
  planConnection(origin: $o, destination: $d, dateTime: $dt, modes: $m, first: 3) {
    edges { node { duration start end legs { mode duration route { shortName } } } }
    routingErrors { code description }
  }
}"""


def slug(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")


def prochaine_arrivee(heure: str, jour: int = 1) -> str:
    """Prochain mardi (jour=1) à l'heure donnée, heure de Québec, en ISO-8601."""
    tz = ZoneInfo("America/Toronto")
    today = date.today()
    delta = (jour - today.weekday()) % 7 or 7
    h, m = map(int, heure.split(":"))
    return datetime.combine(today + timedelta(days=delta), time(h, m), tz).isoformat()


def _loc(lat: float, lng: float) -> dict:
    return {"location": {"coordinate": {"latitude": lat, "longitude": lng}}}


def trajet(otp_url: str, origine: tuple, dest: tuple, mode: str, arrivee: str) -> dict | None:
    """Meilleur itinéraire (le plus court) ; None si aucun."""
    variables = {"o": _loc(*origine), "d": _loc(*dest), "dt": {"latestArrival": arrivee}}
    if MODES[mode]:
        variables["m"] = MODES[mode]
    r = requests.post(otp_url, json={"query": QUERY, "variables": variables}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    edges = body["data"]["planConnection"]["edges"]
    if not edges:
        return None
    best = min((e["node"] for e in edges), key=lambda n: n["duration"])
    return {
        "minutes": round(best["duration"] / 60),
        "etapes": [
            {"mode": l["mode"], "min": round(l["duration"] / 60),
             "ligne": (l.get("route") or {}).get("shortName")}
            for l in best["legs"]
        ],
    }


def create_tables(conn: sqlite3.Connection, destinations: list[dict]) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trajets (
        adresse      TEXT NOT NULL,
        destination  TEXT NOT NULL,   -- slug de la destination : ulaval, travail...
        mode         TEXT NOT NULL,   -- bus, velo, auto
        minutes      INTEGER,         -- NULL : aucun itinéraire trouvé
        etapes       TEXT,            -- JSON : [{mode, min, ligne}, ...]
        calcule_le   TEXT NOT NULL,
        PRIMARY KEY (adresse, destination, mode)
    );
    CREATE TABLE IF NOT EXISTS destinations (
        slug TEXT PRIMARY KEY, nom TEXT, lat REAL, lng REAL
    );
    DELETE FROM destinations;
    """)
    conn.executemany(
        "INSERT INTO destinations VALUES (?, ?, ?, ?)",
        [(slug(d["nom"]), d["nom"], d["lat"], d["lng"]) for d in destinations],
    )
    # Vue avec une colonne par destination × mode, triable dans Datasette.
    cols = ",\n".join(
        f"(SELECT minutes FROM trajets t WHERE t.adresse = a.adresse "
        f"AND t.destination = '{slug(d['nom'])}' AND t.mode = '{m}') AS {slug(d['nom'])}_{m}"
        for d in destinations for m in MODES
    )
    conn.executescript(f"""
    DROP VIEW IF EXISTS annonces_trajets;
    CREATE VIEW annonces_trajets AS
    SELECT a.*{"," if cols else ""}
    {cols}
    FROM annonces a;
    """)
    conn.commit()


def _en_attente(conn: sqlite3.Connection, destinations: list[dict]) -> list[tuple]:
    """Trajets manquants, annonces actives et récentes d'abord."""
    todo = []
    for d in destinations:
        for m in MODES:
            rows = conn.execute(
                """SELECT g.adresse, g.lat, g.lng,
                          (SELECT max(l.actif * 1e10 + strftime('%s', l.premiere_vue))
                             FROM listings l WHERE l.adresse = g.adresse) AS prio
                   FROM geo g
                   WHERE g.lat IS NOT NULL AND NOT EXISTS (
                     SELECT 1 FROM trajets t WHERE t.adresse = g.adresse
                       AND t.destination = ? AND t.mode = ?)""",
                (slug(d["nom"]), m),
            )
            todo += [(prio or 0, adr, lat, lng, d, m) for adr, lat, lng, prio in rows]
    todo.sort(key=lambda t: -t[0])
    return [t[1:] for t in todo]


def calculer(conn: sqlite3.Connection, cfg: dict, limit: int | None = None) -> int | None:
    """Calcule jusqu'à `limit` trajets manquants (défaut : max_par_passage).
    Retourne le nombre restant, ou None si OTP est injoignable."""
    destinations = cfg.get("destinations", [])
    if not destinations:
        return 0
    create_tables(conn, destinations)

    otp_url = cfg.get("otp_url", "http://localhost:8080/otp/gtfs/v1")
    arrivee = prochaine_arrivee(cfg.get("arrivee", "08:30"))
    limit = cfg.get("max_par_passage", 500) if limit is None else limit
    if limit <= 0:
        return None

    try:
        requests.get(otp_url.rsplit("/otp", 1)[0], timeout=5)
    except requests.RequestException:
        log.warning("OTP injoignable (%s) : temps de trajet reportés", otp_url)
        return None

    todo = _en_attente(conn, destinations)
    lot = todo[:limit]

    def un(t):
        adr, lat, lng, d, m = t
        return t, trajet(otp_url, (lat, lng), (d["lat"], d["lng"]), m, arrivee)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = 0
    # Requêtes OTP en parallèle ; écritures SQLite dans ce fil seulement.
    with ThreadPoolExecutor(cfg.get("paralleles", 4)) as pool:
        for f in as_completed([pool.submit(un, t) for t in lot]):
            try:
                (adr, _, _, d, m), res = f.result()
            except Exception as e:
                log.warning("trajet ignoré : %s", e)
                continue
            conn.execute(
                "INSERT OR REPLACE INTO trajets VALUES (?, ?, ?, ?, ?, ?)",
                (adr, slug(d["nom"]), m, res and res["minutes"],
                 json.dumps(res and res["etapes"], ensure_ascii=False), now),
            )
            ok += 1
            if ok % 100 == 0:
                conn.commit()
    conn.commit()
    reste = len(todo) - ok
    if lot:
        log.info("trajets : %d calculés (%d en attente)", ok, reste)
    return reste


def worker(conn: sqlite3.Connection, cfg: dict, pause: int = 60) -> None:
    """Tourne en continu : calcule les trajets manquants par lots, puis attend
    les nouvelles annonces (ou qu'OTP soit prêt) avant de recommencer."""
    lot = cfg.get("lot", 200)
    log.info("worker de trajets démarré (lots de %d, pause %d s)", lot, pause)
    while True:
        reste = calculer(conn, cfg, limit=lot)
        if reste is None or reste <= 0:
            time.sleep(pause)
