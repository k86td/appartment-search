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

Variables UI : `DB` (défaut `listings.db`), `IMAGES` (défaut `images`), `CONFIG` (défaut `config.toml`). Au démarrage, l'UI crée les tables et vues manquantes.

Variables OTP : `OTP_MEM` (défaut `4G`), `OTP_DIR` (défaut `otp-data`), `OTP_PORT`, `OTP_BBOX`.
Quand `[trajets]` est configuré et qu'OTP tourne, chaque passage calcule les temps bus / vélo / auto
vers chaque destination (vue `annonces_trajets`). Sans scraper : `appartment-search --trajets-seulement`.
Pour tout recalculer (ex. après un nouveau graphe) : `DELETE FROM trajets;`.

Isochrones : `appartment-search --isochrones` calcule, pour une grille de points (section
`[isochrones]`), le temps vers chaque destination. Long la première fois (~5 000 points par
destination et par mode), mais reprend où il s'était arrêté. Affichage : menu « Isochrone » de `/carte`.
Pour recalculer : `DELETE FROM isochrones;`.

Relancer `otp-build` quand le RTC change ses horaires (le GTFS est retéléchargé à chaque fois).

### Avec pip

```sh
pip install -e '.[ui]'
appartment-search -c config.toml          # un passage de scraping
datasette listings.db -m metadata.yml     # interface sur http://localhost:8001
```

Les photos sont copiées dans `./images/` (servies par Datasette sous `/images/`), donc elles restent
disponibles après le retrait de l'annonce. Les coordonnées de l'annonceur sont dans `contacts`
(nom, type, téléphones et courriels trouvés dans la description, numéro partiel fourni par le site,
site web, profil).

Tables : `listings` (résumé), `details` (page complète en cache : description, images, JSON brut),
`attributs` (meublé, animaux, chauffage… en format long) et la vue `annonces` qui regroupe tout.

Page **http://localhost:8001/carte** : liste triable (prix, temps de trajet), carte, photos, fiche détaillée et comparaison côte à côte.

## Sites

| Site     | État |
|----------|------|
| Kijiji   | ✅ (JSON `__NEXT_DATA__` des pages de résultats) |
| MSI      | ✅ (API WordPress `imedia/v2/catalog`, tout le catalogue en un appel) |
| Logisco  | ✅ (`/api/search` du site pour la liste, page de l'unité pour l'adresse et les photos) |
| GestiPro | ✅ (API WordPress `wp/v2/properties` ; bloque les IP de centres de données, OK depuis une connexion résidentielle) |

## Ajouter un site

1. Créer `appartment_search/scrapers/<site>.py` avec une classe qui hérite de `Scraper` et implémente `scrape() -> list[Listing]`.
   Optionnel : `fetch_details(source_id, url) -> Details | None` pour ouvrir la page de chaque annonce.
   Les infos (meublé, inclusions, stationnement…) vont dans `Details.caracteristiques`, au format
   **standard** `models.Caracteristiques`, commun à tous les sites (`None` = inconnu). Les attributs
   bruts du site peuvent aussi être gardés dans `Details.attributs`, pour consultation.
   `details_from_raw(raw)` permet de reconstruire les détails depuis le cache sans requête.
2. L'enregistrer dans `scrapers/__init__.py`.
3. Ajouter une section `[scrapers.<site>]` dans `config.toml`.
