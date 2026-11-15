from dataclasses import dataclass, field


@dataclass
class Listing:
    source: str                 # "kijiji", "msi", ...
    source_id: str              # identifiant propre au site
    url: str
    titre: str | None = None
    prix: int | None = None     # $/mois
    taille: str | None = None   # "3½", "4½", ...
    adresse: str | None = None
    disponible: str | None = None
    lat: float | None = None    # optionnel : si le site fournit déjà les coordonnées
    lng: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class Details:
    """Tout ce que la page d'une annonce donne. Récupéré une seule fois par annonce."""
    description: str | None = None
    images: list[str] = field(default_factory=list)
    # nom canonique -> (libellé, valeur lisible, valeur canonique)
    attributs: dict[str, tuple[str | None, str | None, str | None]] = field(default_factory=dict)
    contact: "Contact | None" = None
    raw: dict = field(default_factory=dict)


@dataclass
class Contact:
    nom: str | None = None
    type: str | None = None             # particulier, professionnel...
    telephones: list[str] = field(default_factory=list)
    courriels: list[str] = field(default_factory=list)
    telephone_partiel: str | None = None  # ex. Kijiji masque la fin : 418874xxxx
    site_web: str | None = None
    profil_url: str | None = None
