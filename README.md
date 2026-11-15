# appartment-search

Scrape des sites de logement, stocke les annonces dans SQLite et les affiche avec Datasette.

## Utilisation

### Avec Nix

```sh
nix run .            # un passage de scraping (lit ./config.toml, écrit ./listings.db)
nix run .#ui         # Datasette sur http://localhost:8001 (DB=chemin pour une autre base)
nix develop          # shell avec Python, requests et datasette
```

Depuis ailleurs : `nix run github:k86td/appartment-search -- -c /chemin/config.toml`.

### OpenTripPlanner (temps de trajet, isochrones)

```sh
nix run .#otp-build   # télécharge OSM + GTFS du RTC, construit le graphe dans ./otp-data
nix run .#otp-serve   # API sur http://localhost:8080
```

Variables : `OTP_MEM` (défaut `4G`), `OTP_DIR` (défaut `otp-data`), `OTP_PORT`, `OTP_BBOX`.
Quand `[trajets]` est configuré et qu'OTP tourne, chaque passage calcule les temps bus / vélo / auto
vers chaque destination (vue `annonces_trajets`). Sans scraper : `appartment-search --trajets-seulement`.
Pour tout recalculer (ex. après un nouveau graphe) : `DELETE FROM trajets;`.

Relancer `otp-build` quand le RTC change ses horaires (le GTFS est retéléchargé à chaque fois).

### Avec pip

```sh
pip install -e '.[ui]'
appartment-search -c config.toml          # un passage de scraping
datasette listings.db -m metadata.yml     # interface sur http://localhost:8001
```

Tables : `listings` (résumé), `details` (page complète en cache : description, images, JSON brut),
`attributs` (meublé, animaux, chauffage… en format long) et la vue `annonces` qui regroupe tout.

Page **http://localhost:8001/carte** : liste triable (prix, temps de trajet), carte, photos, fiche détaillée et comparaison côte à côte.

## Sites

| Site     | État |
|----------|------|
| Kijiji   | ✅ (JSON `__NEXT_DATA__` des pages de résultats) |
| MSI      | à faire |
| Logisco  | à faire |
| Gestipro | à faire |

## Ajouter un site

1. Créer `appartment_search/scrapers/<site>.py` avec une classe qui hérite de `Scraper` et implémente `scrape() -> list[Listing]`.
   Optionnel : `fetch_details(source_id, url) -> Details | None` pour ouvrir la page de chaque annonce.
2. L'enregistrer dans `scrapers/__init__.py`.
3. Ajouter une section `[scrapers.<site>]` dans `config.toml`.
