import argparse
import logging
import time
import tomllib

from . import db, dedup, images, isochrones, trajets
from .filters import keep
from .notify import Notifier
from .scrapers import SCRAPERS

log = logging.getLogger("appartment_search")


def run(config: dict) -> None:
    conn = db.connect(config.get("database", "listings.db"))
    notifiers: list[Notifier] = []
    criteria = config.get("filtres", {})
    scrapers = {
        name: SCRAPERS[name](cfg, db.known_ids(conn, name))
        for name, cfg in config.get("scrapers", {}).items()
    }
    db.backfill(conn, scrapers)

    for name, scraper_cfg in config.get("scrapers", {}).items():
        if not scraper_cfg.get("enabled", True):
            continue
        scraper = scrapers[name]
        try:
            listings = scraper.scrape()
        except Exception:
            log.exception("échec du scraper %s", name)
            continue

        if scraper.catalogue_complet():
            db.mark_missing_inactive(conn, name, {l.id for l in listings})
        listings = [l for l in listings if keep(l, criteria)]
        new = db.upsert(conn, listings)
        log.info("%s : %d annonces, %d nouvelles", name, len(listings), len(new))

        fetch_all_details(conn, scraper, scraper_cfg)

        if new:
            for n in notifiers:
                n.notify(new)

    dedup.calculer(conn)

    img = config.get("images", {})
    if img.get("enabled", True):
        images.telecharger(conn, img.get("dossier", "images"), scrapers,
                           img.get("max_par_passage", 1000))

    if "trajets" in config:
        trajets.calculer(conn, config["trajets"])


def fetch_all_details(conn, scraper, cfg: dict) -> None:
    """Ouvre chaque annonce pas encore en cache (nouvelles + échecs précédents)."""
    limit = cfg.get("max_details", 100)
    delay = cfg.get("delay", 2.0)
    pending = db.pending_details(conn, scraper.name, limit)
    ok = gone = 0
    for listing_id, source_id, url in pending:
        try:
            details = scraper.fetch_details(source_id, url)
        except Exception as e:
            log.warning("détails %s : %s (réessai au prochain passage)", listing_id, e)
            continue
        finally:
            time.sleep(delay)
        if details is None:
            db.mark_inactive(conn, listing_id)
            gone += 1
        else:
            db.save_details(conn, listing_id, details)
            ok += 1
    if pending:
        log.info("%s : %d détails récupérés, %d annonces retirées", scraper.name, ok, gone)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("--trajets-seulement", action="store_true",
                        help="ne scrape pas, calcule seulement les temps de trajet manquants")
    parser.add_argument("--db", help="chemin de la base (remplace `database` de la config)")
    parser.add_argument("--init", action="store_true",
                        help="crée/met à jour les tables et vues sans rien scraper (lancé par l'UI)")
    parser.add_argument("--isochrones", action="store_true",
                        help="calcule la grille d'isochrones (long, à faire une fois ; reprend où il était)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with open(args.config, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        if not args.init:
            raise
        config = {}
    if args.db:
        config["database"] = args.db
    if args.init:
        conn = db.connect(config.get("database", "listings.db"))
        trajets.create_tables(conn, config.get("trajets", {}).get("destinations", []))
        conn.executescript(isochrones.SCHEMA)
        dedup.calculer(conn)
        db.backfill(conn, {n: SCRAPERS[n](c, set()) for n, c in config.get("scrapers", {}).items() if n in SCRAPERS})
        return
    if args.isochrones:
        t = config.get("trajets", {})
        conn = db.connect(config.get("database", "listings.db"))
        trajets.create_tables(conn, t.get("destinations", []))
        isochrones.calculer(conn, config.get("isochrones", {}), t.get("destinations", []),
                            t.get("otp_url", "http://localhost:8080/otp/gtfs/v1"), t.get("arrivee", "08:30"))
    elif args.trajets_seulement:
        trajets.calculer(db.connect(config.get("database", "listings.db")), config.get("trajets", {}))
    else:
        run(config)


if __name__ == "__main__":
    main()
