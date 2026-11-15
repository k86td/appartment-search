"""Kijiji : les pages de résultats (Next.js) embarquent un état Apollo dans
<script id="__NEXT_DATA__">, avec l'adresse, les coordonnées et les attributs
de chaque annonce. Aucune page de détail à ouvrir."""

import json
import logging
import re
import time
from urllib.parse import urlsplit, urlunsplit

from ..models import Listing
from .base import Scraper, taille_depuis_texte

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def page_url(url: str, page: int) -> str:
    """Insère /page-N/ avant le dernier segment (k0c37l1700124)."""
    if page == 1:
        return url
    parts = urlsplit(url)
    head, last = parts.path.rstrip("/").rsplit("/", 1)
    return urlunsplit(parts._replace(path=f"{head}/page-{page}/{last}"))


def _attrs(raw: dict) -> dict[str, str]:
    out = {}
    # Pages de résultats : seulement canonicalValues ; pages de détail : values aussi.
    for a in (raw.get("attributes") or {}).get("all", []):
        vals = a.get("canonicalValues") or a.get("values")
        if vals:
            out[a["canonicalName"]] = vals[0]
    return out


def to_listing(raw: dict) -> Listing:
    attrs = _attrs(raw)
    loc = raw.get("location") or {}
    coords = loc.get("coordinates") or {}
    price = (raw.get("price") or {}).get("amount")

    taille = taille_depuis_texte(raw.get("title"))
    chambres = attrs.get("numberbedrooms", "")
    if not taille and chambres.isdigit():
        # Convention québécoise : 1 chambre = 3½, 2 chambres = 4½, ...
        n = int(chambres)
        taille = "studio" if n == 0 else f"{n + 2}½"

    dispo = attrs.get("dateavailable")
    return Listing(
        source="kijiji",
        source_id=str(raw["id"]),
        url=raw["url"],
        titre=raw.get("title"),
        prix=price // 100 if isinstance(price, int) else None,  # en cents
        taille=taille,
        adresse=loc.get("address"),
        disponible=dispo[:10] if dispo else None,
        lat=coords.get("latitude"),
        lng=coords.get("longitude"),
        raw=raw,
    )


def parse_page(html: str) -> list[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ introuvable (structure Kijiji changée ?)")
    state = json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]
    return [v for k, v in state.items() if k.startswith("RealEstateListing:")]


class KijijiScraper(Scraper):
    name = "kijiji"

    def scrape(self) -> list[Listing]:
        urls = self.config.get("urls", [])
        max_pages = self.config.get("max_pages", 5)
        delay = self.config.get("delay", 2.0)

        results: dict[str, Listing] = {}
        for base in urls:
            for page in range(1, max_pages + 1):
                url = page_url(base, page)
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                raws = parse_page(resp.text)
                if not raws:
                    break
                listings = [to_listing(r) for r in raws]
                for l in listings:
                    results[l.id] = l
                log.info("kijiji %s : %d annonces", url, len(listings))

                # Triées par date : si toute la page est déjà connue, on arrête.
                if all(l.id in self.known_ids for l in listings):
                    break
                time.sleep(delay)
        return list(results.values())
