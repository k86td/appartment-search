"""GestiPro (gestipro.info).

Site WordPress (thème Houzez) : l'API REST publique /wp-json/wp/v2/properties
renvoie chaque logement avec prix, adresse, coordonnées, chambres, salles de bain,
animaux, stationnement et la description complète. Les noms des types (« 4 ½ »,
« Maison »…) et des statuts (« Actuellement vacant », « 1er juillet 2027 »…)
viennent de deux autres routes de la même API ; les photos, de /wp/v2/media.

Note : le site refuse les requêtes venant de centres de données ; ce scraper est
fait pour tourner depuis une connexion résidentielle (homelab)."""

import html
import logging
import re
import unicodedata
from datetime import date

from ..models import Caracteristiques, Contact, Details, Listing
from .base import Scraper, taille_depuis_texte, telephones, texte_propre

log = logging.getLogger(__name__)

API = "https://gestipro.info/wp-json/wp/v2"
TEL_BUREAU = "418-522-2525"
CHAMPS = "id,link,title,content,date,modified,property_type,property_status,property_feature,class_list,property_meta"

MOIS = {m: i for i, m in enumerate(["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
                                    "aout", "septembre", "octobre", "novembre", "decembre"], 1)}


def _sans_accents(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def _meta(p: dict, k: str):
    v = (p.get("property_meta") or {}).get(k)
    return v[0] if isinstance(v, list) and v else v


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def disponibilite(statuts: list[str]) -> str | None:
    """« Actuellement vacant » -> aujourd'hui ; « 1er juillet 2027 » -> 2027-07-01."""
    for s in statuts:
        t = _sans_accents(s)
        if "vacant" in t or "maintenant" in t:
            return date.today().isoformat()
        m = re.search(r"(\d{1,2})(?:er)?\s+([a-z]+)\s+(\d{4})", t)
        if m and m.group(2) in MOIS:
            return f"{m.group(3)}-{MOIS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return None


def _inclus(texte: str, *mots: str) -> bool | None:
    """Cherche « chauffage inclus » / « chauffage aux frais du locataire » dans le texte."""
    for ligne in texte.splitlines():
        if not any(m in ligne for m in mots):
            continue
        if re.search(r"(frais|charge)s? (du|des|de la|aux?) locataires?|non inclus|a la charge", ligne):
            return False
        if re.search(r"inclus|compris|fourni", ligne):
            return True
    return None


def _compte(t: str, mot: str) -> float | None:
    """« 5 chambres » / « 2 salles de bain » dans le texte (le plus grand nombre trouvé)."""
    n = [int(m) for m in re.findall(rf"(\d+)\s+{mot}\b", t)]
    return float(max(n)) if n else None


def to_caracteristiques(p: dict, texte: str, types: list[str], features: list[str]) -> Caracteristiques:
    t = _sans_accents(texte)
    f = " ".join(_sans_accents(x) for x in features)
    animaux = _sans_accents(_meta(p, "fave_animaux") or "")
    if "seulement" in animaux or "chat" in animaux:
        anim = "limite"
    elif animaux.startswith(("non", "aucun")) or "animaux ne sont pas" in t:
        anim = "non"
    elif animaux.startswith("oui") or "tolere" in animaux or "accepte" in animaux:
        anim = "oui"
    else:
        anim = None

    if re.search(r"laveuse[^\n]*(inclus|fourni)", t) or "laveuse" in f and "entree" not in f:
        lav = "appareils"
    elif "entree laveuse" in t or "entree-laveuse" in f:
        lav = "entrees"
    elif "salle de lavage" in t or "buanderie" in t:
        lav = "immeuble"
    else:
        lav = None

    pi2 = re.search(r"(\d[\d ,.]{1,6})\s*(pi2|pi²|pieds? carres?|pc\b)", t)
    etage = re.search(r"(\d+)\s*(?:e|er|eme|ieme)\s+etage", t)
    garage = _num(_meta(p, "fave_property_garage"))
    if garage is None:
        garage = 1.0 if re.search(r"stationnement[^\n]*(inclus|compris)", t) else \
                 0.0 if re.search(r"stationnement[^\n]*(rue|vignette|frais|\$)", t) else None
    typ = " ".join(_sans_accents(x) for x in types)

    return Caracteristiques(
        # (?<![a-z]) : ne pas confondre avec « immeuble »
        meuble="semi" if "semi-meubl" in t else "non" if re.search(r"non[ -]meuble", t)
               else "oui" if re.search(r"(?<![a-z])meuble", t) else None,
        animaux=anim,
        chauffage_inclus=_inclus(t, "chauffage"),
        electricite_incluse=_inclus(t, "electricite"),
        eau_chaude_incluse=_inclus(t, "eau chaude"),
        internet_inclus=_inclus(t, "internet", "wi-fi", "wifi"),
        stationnements=int(garage) if garage is not None else None,
        laveuse_secheuse=lav,
        lave_vaisselle=True if re.search(r"lave-vaisselle[^\n]*(inclus|fourni)", t) else None,
        climatisation=True if re.search(r"air climatis|climatiseur|thermopompe", t + " " + f) else None,
        balcon=True if "balcon" in t or "balcon" in f else None,
        rangement=True if "rangement" in t or "rangement" in f else None,
        superficie_pi2=int(_num(re.sub(r"[ ,.]", "", pi2.group(1)))) if pi2 else None,
        etage=int(etage.group(1)) if etage else None,
        chambres=_num(_meta(p, "fave_property_bedrooms")) or _compte(t, r"chambres?"),
        salles_de_bain=_num(_meta(p, "fave_property_bathrooms")) or _compte(t, r"salles? de bains?"),
        type_logement="maison" if "maison" in typ else "condo" if "condo" in typ else "appartement",
    )


class GestiproScraper(Scraper):
    name = "gestipro"
    complet = True

    def __init__(self, config, known_ids):
        super().__init__(config, known_ids)
        self._props: dict[str, dict] = {}
        self._noms: dict[str, dict[int, str]] = {}

    def _get(self, path: str, **params):
        r = self.session.get(f"{API}/{path}", params=params, timeout=30,
                             headers={"Accept": "application/json"})
        r.raise_for_status()
        return r

    def _termes(self, taxo: str) -> dict[int, str]:
        """id -> nom (« 4 ½ », « Maison », « 1er juillet 2027 »…)."""
        if taxo not in self._noms:
            r = self._get(taxo, per_page=100, _fields="id,name")
            self._noms[taxo] = {t["id"]: html.unescape(t["name"]) for t in r.json()}
        return self._noms[taxo]

    def _noms_de(self, p: dict, taxo: str) -> list[str]:
        noms = self._termes(taxo)
        return [noms[i] for i in p.get(taxo) or [] if i in noms]

    def to_listing(self, p: dict) -> Listing | None:
        types = self._noms_de(p, "property_type")
        if any("commercial" in _sans_accents(t) for t in types):
            return None
        titre = html.unescape((p.get("title") or {}).get("rendered") or "")
        contenu = texte_propre((p.get("content") or {}).get("rendered")) or ""
        taille = taille_depuis_texte(contenu[:300]) or next(
            (taille_depuis_texte(t) for t in types if taille_depuis_texte(t)), None)
        lat, lng = _num(_meta(p, "houzez_geolocation_lat")), _num(_meta(p, "houzez_geolocation_long"))
        adresse = (_meta(p, "fave_property_address") or _meta(p, "fave_property_map_address") or titre)
        return Listing(
            source="gestipro",
            source_id=str(p["id"]),
            url=p.get("link"),
            titre=" · ".join(x for x in (titre, taille) if x),
            prix=int(_num(_meta(p, "fave_property_price"))) if _num(_meta(p, "fave_property_price")) else None,
            taille=taille,
            adresse=re.sub(r",\s*Canada$", "", adresse),
            disponible=disponibilite(self._noms_de(p, "property_status")),
            lat=lat, lng=lng,
            raw={"propriete": p, "types": types, "statuts": self._noms_de(p, "property_status"),
                 "caracteristiques": self._noms_de(p, "property_feature")},
        )

    def scrape(self) -> list[Listing]:
        out, page, pages = [], 1, 1
        while page <= pages:
            r = self._get("properties", per_page=100, page=page, _fields=CHAMPS)
            pages = int(r.headers.get("X-WP-TotalPages", 1))
            for p in r.json():
                l = self.to_listing(p)
                if l:
                    self._props[l.source_id] = l.raw
                    out.append(l)
            page += 1
        log.info("gestipro : %d logements", len(out))
        return out

    def _images(self, p: dict) -> list[str]:
        ids = (p.get("property_meta") or {}).get("fave_property_images") or []
        if not ids:
            return []
        r = self._get("media", include=",".join(ids), per_page=100, _fields="id,source_url,media_details")
        par_id = {}
        for m in r.json():
            tailles = (m.get("media_details") or {}).get("sizes") or {}
            grand = (tailles.get("large") or tailles.get("full") or {}).get("source_url")
            par_id[str(m["id"])] = grand or m.get("source_url")
        return [par_id[i] for i in ids if par_id.get(i)]   # dans l'ordre de la galerie

    def fetch_details(self, source_id: str, url: str) -> Details | None:
        raw = self._props.get(source_id)
        if raw is None:
            return None                                     # plus dans le catalogue
        d = self.details_from_raw(raw)
        try:
            d.images = self._images(raw["propriete"])
        except Exception as e:
            log.warning("gestipro %s : photos indisponibles (%s)", source_id, e)
        return d

    def details_from_raw(self, raw: dict) -> Details | None:
        p = raw.get("propriete")
        if not p:
            return None
        texte = texte_propre((p.get("content") or {}).get("rendered")) or ""
        return Details(
            description=texte or None,
            caracteristiques=to_caracteristiques(p, texte, raw.get("types", []), raw.get("caracteristiques", [])),
            attributs={k: (k, str(v[0]) if isinstance(v, list) and v else str(v), None)
                       for k, v in (p.get("property_meta") or {}).items()
                       if k.startswith("fave_") and k not in ("fave_property_images", "fave_virtual_tour")},
            contact=Contact(
                nom="GestiPro",
                type="professionnel",
                telephones=telephones(texte) or [TEL_BUREAU],
                site_web="https://gestipro.info",
                profil_url=p.get("link"),
            ),
            raw=raw,
        )
