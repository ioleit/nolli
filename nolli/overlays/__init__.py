"""nolli's overlays on top of the extractors': what a change did (``change``, with git's hunks
kept as the ``content`` map), a review's verdicts (``verdicts``), how the picture names a box
(``label``), its hover text (``hover``) and a review's card (``review``)."""

from __future__ import annotations

from ..scene import MapStore, Overlay


def make_overlays(maps: MapStore) -> list[Overlay]:
    from .change import Change
    from .hover import Hover
    from .label import Label
    from .review import Review
    from .verdicts import Verdicts

    return [Change(maps.open_maps("content")), Verdicts(maps.open_maps("verdicts")), Label(), Hover(), Review()]
