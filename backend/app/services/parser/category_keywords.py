"""Phase 2 Task T-H: EN + zh keyword catalogue for the category matcher.

The dictionary in this module maps each of the 23 seeded builder
category names (see :mod:`app.core.seed`) to a tuple of keywords that
the parser uses to guess the category of a free-text expense string.

Keyword normalisation
---------------------
Tokens coming out of :func:`app.services.parser.tokens.tokenize` carry a
``.normalized`` field produced by
:func:`app.core.text.normalize_alias`. The category matcher looks up
each token's normal against this dictionary's values, so the values
stored here MUST be in the normalised form too. We derive them by
running :func:`normalize_alias` at module-import time over a small
authoring-friendly source dictionary; the public export
:data:`CATEGORY_KEYWORDS` is the post-normalisation version and is what
the matcher consumes.

Dictionary keys
---------------
Keys MUST exactly match the ``category_name`` values inserted by
``seed_builder_categories``. ``seed_builder_categories`` is idempotent
and runs on every boot (+ every test via the ``seed_categories``
fixture), so the match is what lets the matcher translate a
``category_name`` back into a ``category_id`` at query time.

"tiles" disambiguation
----------------------
The word ``"tiles"`` is equally plausible as a Roofing keyword (roof
tiles) and a Tiling keyword (wall / floor tiles). To avoid every
``"tiles"`` mention being a tied, ambiguous two-category hit, the
dictionary below puts ``"tiles"`` **only** under Tiling: in the
bluecollar-note style the parser actually sees (``"tiles $250 Kelly"``
rather than ``"roof tiles"``) the word almost always refers to floor
or wall tiles. Roofing still has multiple distinctive keywords
(``roofing``, ``roof``, ``屋顶``) so it remains reachable by anchors
that aren't ambiguous; ``"roof tiles"`` still wins Roofing via the
``roof`` keyword.
"""

from __future__ import annotations

from app.core.text import normalize_alias

# Authoring-friendly source dictionary: human-readable EN + zh keyword
# strings per category. These are NOT what the matcher consumes — they
# get normalised at module load (see ``CATEGORY_KEYWORDS`` below).
_RAW_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Demolition": ("demolition", "demo", "拆除"),
    "Earthworks": (
        "earthworks",
        "excavator",
        "excavation",
        "bluemetal",
        "gravel",
        "sand",
        "dirt",
        "泥土",
        "挖掘",
    ),
    "Concrete": ("concrete", "cement", "slab", "混凝土", "水泥"),
    "Brickwork": ("brickwork", "bricks", "mortar", "砖", "砌砖"),
    "Carpentry": (
        "carpentry",
        "timber",
        "framing",
        "lumber",
        "wood",
        "木工",
        "木材",
    ),
    "Roofing": ("roofing", "roof", "屋顶"),
    "Cladding": ("cladding", "weatherboard", "siding", "外墙"),
    "Waterproofing": ("waterproofing", "waterproof", "防水"),
    "Plumbing": (
        "plumbing",
        "plumber",
        "pvc",
        "pipes",
        "水管",
        "水工",
        "水工材料",
    ),
    "Electrical": ("electrical", "sparky", "cable", "wiring", "电工", "电线"),
    "Gyprock": ("gyprock", "plasterboard", "drywall", "石膏板"),
    "Painting": ("painting", "paint", "painter", "油漆"),
    "Flooring": ("flooring", "laminate", "carpet", "地板"),
    # "tiles" kept only under Tiling — see module docstring.
    "Tiling": ("tiling", "tiles", "tiler", "瓷砖"),
    "Joinery": ("joinery", "cabinets", "vanity", "橱柜"),
    "Windows & Doors": ("windows", "doors", "glazing", "窗户", "门"),
    "Structural Steel": (
        "structuralsteel",
        "steel",
        "beams",
        "rsj",
        "钢材",
        "钢",
    ),
    "Labour": ("labour", "labor", "wages", "人工", "工资"),
    "Preliminaries": (
        "preliminaries",
        "prelims",
        "hoarding",
        "scaffold",
        "scaffolding",
        "脚手架",
    ),
    "Equipment Hire": ("equipmenthire", "hire", "rental", "租赁"),
    "Waste / Skip Bin": ("waste", "skip", "skipbin", "垃圾", "垃圾桶"),
    "Delivery": ("delivery", "freight", "shipping", "运费"),
    "Miscellaneous": ("miscellaneous", "misc", "other", "其他"),
}


def _normalise_keywords(
    raw: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Run every keyword through ``normalize_alias``; drop empties + dups.

    Keeping this as a helper means the authoring dict stays
    human-readable while the exported dict is ready for cheap
    equality-compare lookups.
    """
    out: dict[str, tuple[str, ...]] = {}
    for category_name, keywords in raw.items():
        seen: list[str] = []
        for kw in keywords:
            normalised = normalize_alias(kw)
            if not normalised:
                continue
            if normalised in seen:
                continue
            seen.append(normalised)
        out[category_name] = tuple(seen)
    return out


# Public export consumed by :mod:`app.services.parser.categories`. All
# values are already :func:`normalize_alias`-folded so the matcher can
# compare against ``token.normalized`` with plain ``==`` / ``in``.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = _normalise_keywords(_RAW_CATEGORY_KEYWORDS)
