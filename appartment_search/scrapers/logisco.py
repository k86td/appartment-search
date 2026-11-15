"""LOGISCO (logisco.com).

Le site a un moteur de recherche (GET /api/search, style Meilisearch) qui renvoie
toutes les unités disponibles d'un coup, avec prix, grandeur, superficie, date,
coordonnées et caractéristiques. La page de chaque unité ajoute l'adresse complète
(code postal), le plan et la galerie de photos."""

import logging
import re
from bs4 import BeautifulSoup

from ..models import Contact, Details, Listing
from .base import Scraper, taille_depuis_texte, telephones

log = logging.getLogger(__name__)

API = "https://logisco.com/api/search"
TYPE_UNITE = "990a8ebe-9b92-4405-a677-c28224679724"   # page_type_id des pages « Unité »
TEL_DEFAUT = "1-833-564-4726"

# Codes LOGISCO -> noms d'attributs Kijiji (pour la vue `annonces`).
CODES_KIJIJI = {
    "HEATING": "heat",
    "ELECTRICITY": "hydro",
    "HOT_WATER": "water",
    "WIFI": "internet",
    "AC": "airconditioning",
    "WASHER_DRYER": "laundryinunit",
    "ENTRANCE_WASHER_DRYER": "laundryinunit",
    "DISHWASHER_CONNECTION": "dishwasher",
    "ELEVATOR": "elevator",
    "GYM": "gym",
    "DOG_ACCEPTED": "petsallowed",
    "STORAGE": "storagelocker",
}
PARKING = {"INDOOR_PARKING": 1, "OUTDOOR_PARKING": 1, "OUTDOOR_OR_INDOOR_PARKING": 1, "DOUBLE_PARKING": 2}

GROUPES = [  # (code d'attribut, titre dans la description)
    ("Amenities", "Inclus dans le loyer"),
    ("UnitFeatures", "Particularités"),
    ("Services", "Services aux locataires"),
    ("CommonAreas", "Espaces communs"),
]


def _vals(hit: dict, code: str) -> list[dict]:
    return (hit.get("attributes") or {}).get(code) or []


def _val(hit: dict, code: str):
    v = _vals(hit, code)
    return v[0]["value"] if v else None


def _chemin(hit: dict) -> list[str]:
    # "Accueil / Appartements à louer / Lévis / Saint-David / ONYX / Appartement 318"
    return [p.strip() for p in (hit.get("complete_name") or "").split("/")]


def to_listing(hit: dict) -> Listing:
    chemin = _chemin(hit)
    ville = chemin[2] if len(chemin) > 2 else None
    projet = chemin[-2] if len(chemin) > 3 else None
    rue = (hit.get("addresses") or {}).get("NumberStreet")
    # value vaut 0 quand la date est passée (disponible maintenant) : on lit le code.
    dispo = (_vals(hit, "AvailableAt") or [{}])[0].get("code")
    geo = hit.get("_geo") or {}
    taille = _val(hit, "Size")
    return Listing(
        source="logisco",
        source_id=hit["id"],
        url=hit["url"],
        titre=" · ".join(x for x in (projet, hit.get("name"), taille) if x),
        prix=int(_val(hit, "Price")) if _val(hit, "Price") else None,
        taille=taille_depuis_texte(taille) or ("studio" if taille == "Studio" else taille),
        adresse=", ".join(x for x in (rue, ville, "QC") if x),
        disponible=dispo[:10] if dispo else None,
        lat=geo.get("lat") or _val(hit, "Latitude"),
        lng=geo.get("lng") or _val(hit, "Longitude"),
        # imageUrls : vignette de la carte, affichée tant que la page de l'unité n'est pas ouverte.
        raw={**{k: v for k, v in hit.items() if k != "html"},
             "imageUrls": re.findall(r'<img[^>]+src="([^"]+)"', hit.get("html") or "")[:1]},
    )


def to_details(hit: dict, page: BeautifulSoup | None) -> Details:
    attributs = {}
    lignes = []
    for code, titre in GROUPES:
        vals = _vals(hit, code)
        if vals:
            lignes.append(f"{titre} :\n" + "\n".join(f"• {v['value']}" for v in vals))
        for v in vals:
            attributs[v["code"].lower()] = (v["value"], "Yes", "1")
            if v["code"] in CODES_KIJIJI:
                attributs[CODES_KIJIJI[v["code"]]] = (v["value"], "Yes", "1")
    for k in ("heat", "hydro", "furnished", "petsallowed"):
        attributs.setdefault(k, (k, "No", "0"))
    places = max((PARKING.get(v["code"], 0) for v in _vals(hit, "Amenities")), default=0)
    attributs["numberparkingspots"] = ("Stationnement inclus", str(places), str(places))
    if _val(hit, "SurfaceArea"):
        attributs["areainfeet"] = ("Superficie (pi²)", str(_val(hit, "SurfaceArea")), str(_val(hit, "SurfaceArea")))
    if _val(hit, "Floor"):
        attributs["floor"] = ("Étage", str(_val(hit, "Floor")), str(_val(hit, "Floor")))
    if _val(hit, "NumberOfBedrooms") is not None:
        n = str(_val(hit, "NumberOfBedrooms"))
        attributs["numberbedrooms"] = ("Chambres", n, n)

    images, adresse, tel = [], None, None
    if page:
        for img in page.select("img"):
            src = img.get("src") or img.get("data-src") or ""
            # Photos et plan en grand format ; les vignettes d'articles (-475) sont ignorées.
            if "/static/vb/" in src and ("-1200/" in src or "-1800/" in src) and src not in images:
                images.append(src)
        header = page.select_one(".unitHeader-text")
        if header:
            txt = header.get_text(" ", strip=True)
            # « ... Appartement 318 • ONYX 318-3900 boulevard Guillaume-Couture, Lévis, Québec, G6W 0W8 »
            if "•" in txt:
                adresse = txt.split("•")[-1].strip()
                projet = _chemin(hit)[-2] if len(_chemin(hit)) > 3 else ""
                if projet and adresse.lower().startswith(projet.lower()):
                    adresse = adresse[len(projet):].strip()
        a = page.select_one('a[href^="tel:"]')
        tel = a.get_text(strip=True) if a else None

    chemin = _chemin(hit)
    voisinage = [v["code"].replace("_", " ").lower() for v in _vals(hit, "NeighbourhoodServices")]
    if voisinage:
        lignes.append("À proximité :\n" + ", ".join(voisinage))
    if adresse:
        lignes.insert(0, adresse)
    return Details(
        description="\n\n".join(lignes) or None,
        images=images,
        attributs=attributs,
        contact=Contact(
            nom="LOGISCO" + (f" — {chemin[-2]}" if len(chemin) > 3 else ""),
            type="professionnel",
            telephones=telephones(tel) or [TEL_DEFAUT],
            site_web="https://logisco.com",
            profil_url=hit.get("url"),
        ),
        raw={"hit": {k: v for k, v in hit.items() if k != "html"}, "adresse_complete": adresse},
    )


class LogiscoScraper(Scraper):
    name = "logisco"
    complet = True

    def __init__(self, config, known_ids):
        super().__init__(config, known_ids)
        self._hits: dict[str, dict] = {}

    def scrape(self) -> list[Listing]:
        out, page = [], 1
        while True:
            resp = self.session.get(API, params=[
                ("filters[]", f'page_type_id = "{TYPE_UNITE}"'),
                ("filters[]", "attributes.Available.code = 1"),
                ("filters[]", "attributes.ProjectType.code = RESIDENTIAL"),
                ("page", page), ("limit", 500),
            ], headers={"Accept": "application/json"}, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            for hit in (data.get("hits") or {}).values():
                if (_vals(hit, "UnitType") or [{}])[0].get("code") != "APARTMENT":
                    continue
                self._hits[hit["id"]] = hit
                out.append(to_listing(hit))
            if page >= (data.get("pageCount") or 1):
                break
            page += 1
        log.info("logisco : %d unités disponibles", len(out))
        return out

    def fetch_details(self, source_id: str, url: str) -> Details | None:
        hit = self._hits.get(source_id)
        if hit is None:
            return None  # plus dans les unités disponibles
        resp = self.session.get(url, timeout=30)
        page = BeautifulSoup(resp.text, "html.parser") if resp.ok else None
        return to_details(hit, page)

    def contact_from_raw(self, raw: dict) -> Contact | None:
        return to_details(raw["hit"], None).contact if "hit" in raw else None
