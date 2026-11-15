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
class Caracteristiques:
    """Format STANDARD, indépendant des sites : chaque scraper convertit ses propres
    champs vers celui-ci. None = inconnu (le site ne le dit pas), jamais « non » par défaut.
    C'est ce format que l'interface, les filtres et la déduplication utilisent."""
    meuble: str | None = None            # "oui" | "non" | "semi"
    animaux: str | None = None           # "oui" | "non" | "limite" (ex. chats seulement, à discuter)
    chauffage_inclus: bool | None = None
    electricite_incluse: bool | None = None
    eau_chaude_incluse: bool | None = None
    internet_inclus: bool | None = None
    stationnements: int | None = None    # places incluses
    laveuse_secheuse: str | None = None  # "appareils" | "entrees" | "immeuble" | "non"
    lave_vaisselle: bool | None = None
    climatisation: bool | None = None
    ascenseur: bool | None = None
    balcon: bool | None = None
    rangement: bool | None = None
    gym: bool | None = None
    piscine: bool | None = None
    superficie_pi2: int | None = None
    etage: int | None = None
    chambres: float | None = None
    salles_de_bain: float | None = None
    type_logement: str | None = None     # "appartement" | "condo" | "maison" | "sous-sol" | ...
    bail: str | None = None              # "12 mois" | "mois à mois" | "cession" | ...
    fumeur: bool | None = None

    CHAMPS = None  # rempli plus bas


@dataclass
class Details:
    """Tout ce que la page d'une annonce donne. Récupéré une seule fois par annonce."""
    description: str | None = None
    images: list[str] = field(default_factory=list)
    caracteristiques: Caracteristiques = field(default_factory=lambda: Caracteristiques())
    # Attributs bruts, avec les noms du site d'origine (pour consultation seulement) :
    # nom -> (libellé, valeur lisible, valeur canonique)
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


Caracteristiques.CHAMPS = [f for f in Caracteristiques.__dataclass_fields__ if f != "CHAMPS"]
