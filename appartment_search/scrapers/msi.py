"""MSI Gestion immobilière (msimmobiliers.com).

Le site (Next.js) lit un seul endpoint WordPress qui renvoie tout le catalogue :
régions > villes > arrondissements > quartiers > logements ou projets (immeubles)
contenant des logements. Un appel suffit, détails compris ; rien à ouvrir par annonce."""

import logging

from ..models import Contact, Details, Listing
from .base import Scraper, taille_depuis_texte, telephones, texte_propre

log = logging.getLogger(__name__)

API = "https://api.msimmobiliers.com/wp-json/imedia/v2/catalog"
SITE = "https://msimmobiliers.com"
TEL_BUREAU = "418-681-7888"   # bureau de Québec (wp-json/imedia/v2/settings)
TYPES_EXCLUS = {"Rangement", "Local commercial", "Stationnement extérieur", "Entrepôt"}

# Équivalences vers les noms d'attributs de Kijiji, pour que la vue `annonces`
# (meublé, chauffage, hydro...) fonctionne pour toutes les sources.
FACILITES_KIJIJI = {
    "heating_included": "heat",
    "electricity_included": "hydro",
    "hotwater_included": "water",
    "internet_included": "internet",
    "furnished": "furnished",
    "washer_entrance": "laundryinunit",
    "dishwasher_entrance": "dishwasher",
    "air_conditioning": "airconditioning",
    "elevator": "elevator",
    "balcony": "balcony",
    "gym": "gym",
    "pool": "pool",
    "bicycle_parking": "bicycleparking",
    "locker": "storagelocker",
}


def _fr(v):
    return v.get("fr") if isinstance(v, dict) else v


def _logements(node, parent=None):
    """Parcourt l'arbre du catalogue : (logement, projet ou None)."""
    if isinstance(node, dict):
        for r in node.get("rentals") or []:
            if r.get("isProject"):
                for u in r.get("rentals") or []:
                    yield u, r
            else:
                yield r, None
        for k, v in node.items():
            if k != "rentals":
                yield from _logements(v)
    elif isinstance(node, list):
        for e in node:
            yield from _logements(e)


def _images(*objs) -> list[str]:
    urls = []
    for o in objs:
        if not o:
            continue
        for g in o.get("gallery") or []:
            u = (g.get("sizes") or {}).get("large")
            if u and u not in urls:
                urls.append(u)
    if not urls:
        for o in objs:
            u = ((o or {}).get("thumbnail") or {}).get("sizes", {}).get("large")
            if u:
                urls.append(u)
                break
    return urls


def to_listing_details(u: dict, projet: dict | None) -> tuple[Listing, Details]:
    lieu = projet or u
    addr = lieu.get("address") or {}
    typ = _fr((u.get("type") or {}).get("name"))
    permalink = _fr(lieu.get("permalink"))

    facilites = [f for f in (u.get("facilities") or []) if isinstance(f, dict) and f.get("slug")]
    notes = [_fr(n) for n in (u.get("secondaryFacilities") or []) + (lieu.get("secondaryFacilities") or []) if n]
    slugs = {f["slug"] for f in facilites}
    attributs = {
        f["slug"]: (_fr(f.get("name")), "Yes", "1") for f in facilites
    }
    for msi, kijiji in FACILITES_KIJIJI.items():
        if msi in slugs:
            attributs[kijiji] = attributs[msi]
    if "half_furnished" in slugs and "furnished" not in slugs:
        attributs["furnished"] = ("Semi-meublé", "Semi-meublé", "0.5")
    attributs.setdefault("furnished", ("Meublé", "No", "0"))
    attributs["petsallowed"] = ("Chien accepté", "Yes" if u.get("dogAllowed") else "No",
                                "1" if u.get("dogAllowed") else "0")
    attributs["numberparkingspots"] = ("Stationnement inclus",
                                       "1" if "parking_included" in slugs else "0",
                                       "1" if "parking_included" in slugs else "0")
    if u.get("area"):
        attributs["areainfeet"] = ("Superficie (pi²)", str(u["area"]), str(u["area"]))
    for cle in ("floor", "rooms", "reference"):
        if u.get(cle) is not None:
            attributs[cle] = (cle, str(u[cle]), str(u[cle]))
    for cle in ("walkScore", "transitScore", "bikeScore"):
        if lieu.get(cle):
            attributs[cle] = (cle, str(lieu[cle]), str(lieu[cle]))

    desc = texte_propre(_fr(lieu.get("description"))) or ""
    if notes:
        desc += "\n\n" + "\n".join(f"• {n}" for n in dict.fromkeys(notes))
    desc = desc.strip() or None
    phone = lieu.get("phone")
    listing = Listing(
        source="msi",
        source_id=str(u["id"]),
        url=SITE + permalink if permalink else SITE,
        titre=" · ".join(x for x in (_fr(projet.get("title")) if projet else None,
                                     u.get("title"), typ) if x),
        prix=int(u["price"]) if u.get("price") else None,
        taille=taille_depuis_texte(typ) or ("studio" if typ and "Studio" in typ else typ),
        adresse=addr.get("address"),
        disponible=(u.get("availability") or "")[:10] or None,
        lat=addr.get("lat"),
        lng=addr.get("lng"),
        raw={"logement": {k: v for k, v in u.items() if k != "rentals"},
             "projet": {k: v for k, v in (projet or {}).items() if k != "rentals"} or None},
    )
    details = Details(
        description=desc,
        images=_images(u, projet) if projet else _images(u),
        attributs=attributs,
        contact=Contact(
            nom="MSI Gestion immobilière" + (f" — {_fr(projet.get('title'))}" if projet else ""),
            type="professionnel",
            telephones=telephones(phone) or telephones(desc) or [TEL_BUREAU],
            site_web=lieu.get("website") or lieu.get("visitBookingWebsite") or None,
            profil_url=listing.url,
        ),
        raw=listing.raw,
    )
    return listing, details


class MsiScraper(Scraper):
    name = "msi"
    complet = True

    def __init__(self, config, known_ids):
        super().__init__(config, known_ids)
        self._details: dict[str, Details] = {}

    def scrape(self) -> list[Listing]:
        regions = set(self.config.get("regions", ["capitale-nationale"]))
        resp = self.session.get(API, timeout=60)
        resp.raise_for_status()
        out = []
        for region in resp.json():
            if _fr(region.get("slug")) not in regions:
                continue
            for u, projet in _logements(region):
                cat = (u.get("category") or {}).get("slug")
                typ = _fr((u.get("type") or {}).get("name"))
                if cat not in ("apartment", "house") or typ in TYPES_EXCLUS:
                    continue
                if u.get("status") not in (None, "publish"):
                    continue
                listing, details = to_listing_details(u, projet)
                self._details[listing.source_id] = details
                out.append(listing)
        log.info("msi : %d logements", len(out))
        return out

    def fetch_details(self, source_id: str, url: str) -> Details | None:
        # Tout est déjà dans le catalogue. Absent du catalogue = loué/retiré.
        return self._details.get(source_id)

    def contact_from_raw(self, raw: dict) -> Contact | None:
        if "logement" not in raw:
            return None
        return to_listing_details(raw["logement"], raw.get("projet"))[1].contact
