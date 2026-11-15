import html
import re
from abc import ABC, abstractmethod

import requests

from ..models import Contact, Details, Listing

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class Scraper(ABC):
    name: str
    # True si scrape() renvoie TOUTES les annonces du site : celles qui n'y sont
    # plus sont alors marquées inactives (louées ou retirées).
    complet: bool = False

    def __init__(self, config: dict, known_ids: set[str]):
        self.config = config
        self.known_ids = known_ids  # ids déjà en base, pour éviter des requêtes inutiles
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    @abstractmethod
    def scrape(self) -> list[Listing]: ...

    def fetch_details(self, source_id: str, url: str) -> Details | None:
        """Ouvre la page d'une annonce. None si elle n'existe plus.
        Par défaut : pas de détails (pour les sites où la liste suffit)."""
        return None

    def contact_from_raw(self, raw: dict) -> Contact | None:
        """Reconstruit le contact depuis details.raw (rattrapage des anciennes annonces)."""
        return None

    def image_download_url(self, url: str) -> str:
        """URL à télécharger pour le cache local (ex. meilleure résolution)."""
        return url


_BLOCK_RE = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6])\b[^>]*>", re.IGNORECASE)
_LI_RE = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def texte_propre(desc: str | None) -> str | None:
    """HTML d'annonce -> texte : sauts de ligne conservés, balises et entités retirées."""
    if not desc or "<" not in desc and "&" not in desc:
        return desc
    t = _BLOCK_RE.sub("\n", desc)
    t = _LI_RE.sub("\n• ", t)
    t = html.unescape(_TAG_RE.sub("", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def telephones(texte: str | None) -> list[str]:
    """Numéros nord-américains trouvés dans un texte, normalisés 418-555-1234."""
    out = []
    for a, b, c in _PHONE_RE.findall(texte or ""):
        n = f"{a}-{b}-{c}"
        if n not in out:
            out.append(n)
    return out


def courriels(texte: str | None) -> list[str]:
    return list(dict.fromkeys(_EMAIL_RE.findall(texte or "")))


_TAILLE_RE = re.compile(r"\b([1-9])\s*(?:½|1/2|et\s+demi|\.5)", re.IGNORECASE)


def taille_depuis_texte(texte: str | None) -> str | None:
    """'3 1/2', '3½', '3 et demi' -> '3½'."""
    if not texte:
        return None
    m = _TAILLE_RE.search(texte)
    return f"{m.group(1)}½" if m else None
