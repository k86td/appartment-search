import re
from abc import ABC, abstractmethod

import requests

from ..models import Details, Listing

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class Scraper(ABC):
    name: str

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


_TAILLE_RE = re.compile(r"\b([1-9])\s*(?:½|1/2|et\s+demi|\.5)", re.IGNORECASE)


def taille_depuis_texte(texte: str | None) -> str | None:
    """'3 1/2', '3½', '3 et demi' -> '3½'."""
    if not texte:
        return None
    m = _TAILLE_RE.search(texte)
    return f"{m.group(1)}½" if m else None
