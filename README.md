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

### Avec pip

```sh
pip install -e '.[ui]'
appartment-search -c config.toml          # un passage de scraping
datasette listings.db -m metadata.yml     # interface sur http://localhost:8001
```

La requête « nouvelles » dans Datasette affiche une carte (via `datasette-cluster-map`).

## Sites

| Site     | État |
|----------|------|
| Kijiji   | ✅ (JSON `__NEXT_DATA__` des pages de résultats) |
| MSI      | à faire |
| Logisco  | à faire |
| Gestipro | à faire |

## Ajouter un site

1. Créer `appartment_search/scrapers/<site>.py` avec une classe qui hérite de `Scraper` et implémente `scrape() -> list[Listing]`.
2. L'enregistrer dans `scrapers/__init__.py`.
3. Ajouter une section `[scrapers.<site>]` dans `config.toml`.
