"""Kijiji : les pages de résultats (Next.js) embarquent un état Apollo dans
<script id="__NEXT_DATA__">, avec l'adresse, les coordonnées et les attributs
de chaque annonce. Aucune page de détail à ouvrir."""

import json
import logging
import re
import time
from urllib.parse import urlsplit, urlunsplit

from ..models import Contact, Details, Listing
from .base import Scraper, courriels, taille_depuis_texte, telephones, texte_propre

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


def apollo_state(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ introuvable (structure Kijiji changée ?)")
    return json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]


def parse_page(html: str) -> list[dict]:
    state = apollo_state(html)
    return [v for k, v in state.items() if k.startswith("RealEstateListing:")]


def resolve_refs(obj, state: dict, depth: int = 0):
    """Remplace les {"__ref": "Type:id"} d'Apollo par l'objet référencé."""
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        if set(obj) == {"__ref"} and obj["__ref"] in state:
            return resolve_refs(state[obj["__ref"]], state, depth + 1)
        return {k: resolve_refs(v, state, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(v, state, depth + 1) for v in obj]
    return obj


def to_contact(raw: dict, state: dict) -> Contact:
    """Infos de l'annonceur visibles sur la page. Kijiji masque la fin du numéro
    (bouton « afficher ») : on garde le préfixe et on cherche le numéro complet
    dans la description, où les proprios le mettent souvent."""
    poster = raw.get("posterInfo") or {}
    pid = poster.get("posterId")
    profil = next((v for k, v in state.items() if pid and k.endswith(f":{pid}")), {})
    path = profil.get("profilePath")
    desc = texte_propre(raw.get("description"))
    return Contact(
        nom=profil.get("name"),
        type={"FSBO": "particulier", "KMB": "professionnel"}.get(poster.get("sellerType"), poster.get("sellerType")),
        telephones=telephones(desc),
        courriels=courriels(desc),
        telephone_partiel=poster.get("phoneNumber"),
        site_web=poster.get("websiteUrl") or profil.get("websiteUrl"),
        profil_url=f"https://www.kijiji.ca{path}" if path else None,
    )


def to_details(raw: dict, others: dict) -> Details:
    attributs = {}
    for a in (raw.get("attributes") or {}).get("all", []):
        vals = a.get("values") or a.get("canonicalValues") or []
        canon = a.get("canonicalValues") or []
        attributs[a["canonicalName"]] = (
            a.get("name"),
            ", ".join(vals) or None,
            ", ".join(canon) or None,
        )
    return Details(
        description=texte_propre(raw.get("description")),
        images=raw.get("imageUrls") or [],
        attributs=attributs,
        contact=to_contact(raw, others.get("autres", {})),
        raw={"listing": raw, **others},
    )


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

    def contact_from_raw(self, raw: dict) -> Contact | None:
        if "listing" not in raw:
            return None
        return to_contact(raw["listing"], raw.get("autres", {}))

    def image_download_url(self, url: str) -> str:
        rule = self.config.get("image_rule", "kijijica-1600-webp")
        return url.split("?", 1)[0] + f"?rule={rule}" if "media.kijiji.ca" in url else url

    def fetch_details(self, source_id: str, url: str) -> Details | None:
        resp = self.session.get(url, timeout=30)
        if resp.status_code in (404, 410):
            return None  # annonce retirée
        resp.raise_for_status()
        state = apollo_state(resp.text)
        key = f"RealEstateListing:{source_id}"
        if key not in state:
            return None
        raw = resolve_refs(state[key], state)
        # Le reste de l'état (profil de l'annonceur, catégories...) est gardé tel quel.
        others = {k: v for k, v in state.items() if k not in (key, "ROOT_QUERY")}
        return to_details(raw, {"autres": others})
