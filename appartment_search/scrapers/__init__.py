from .kijiji import KijijiScraper
from .logisco import LogiscoScraper
from .msi import MsiScraper

SCRAPERS = {
    "kijiji": KijijiScraper,
    "msi": MsiScraper,
    "logisco": LogiscoScraper,
}
