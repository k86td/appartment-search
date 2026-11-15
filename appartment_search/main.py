import argparse
import logging
import tomllib

from . import db
from .filters import keep
from .notify import Notifier
from .scrapers import SCRAPERS

log = logging.getLogger("appartment_search")


def run(config: dict) -> None:
    conn = db.connect(config.get("database", "listings.db"))
    notifiers: list[Notifier] = []
    criteria = config.get("filtres", {})

    for name, scraper_cfg in config.get("scrapers", {}).items():
        if not scraper_cfg.get("enabled", True):
            continue
        scraper = SCRAPERS[name](scraper_cfg, db.known_ids(conn, name))
        try:
            listings = scraper.scrape()
        except Exception:
            log.exception("échec du scraper %s", name)
            continue

        listings = [l for l in listings if keep(l, criteria)]
        new = db.upsert(conn, listings)
        log.info("%s : %d annonces, %d nouvelles", name, len(listings), len(new))

        if new:
            for n in notifiers:
                n.notify(new)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default="config.toml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, "rb") as f:
        run(tomllib.load(f))


if __name__ == "__main__":
    main()
