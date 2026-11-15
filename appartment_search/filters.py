from .models import Listing


def keep(listing: Listing, criteria: dict) -> bool:
    """Critères optionnels lus dans config.toml, section [filtres]."""
    prix_max = criteria.get("prix_max")
    if prix_max and listing.prix and listing.prix > prix_max:
        return False

    tailles = criteria.get("tailles")
    if tailles and listing.taille and listing.taille not in tailles:
        return False

    return True
