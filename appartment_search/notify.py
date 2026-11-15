from typing import Protocol

from .models import Listing


class Notifier(Protocol):
    def notify(self, listings: list[Listing]) -> None: ...


# Aucune implémentation pour l'instant : main.py utilise une liste vide.
