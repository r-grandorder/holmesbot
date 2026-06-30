"""Category filters for the /guess commands.

Optional class / rarity / attribute / trait params that narrow the eligible pool
(they AND together). Discord caps a single choice list at 25, so the trait list is a
curated subset -- most Atlas traits are noise (e.g. "humanoid"). `from_params` turns
the chosen options into a ServantFilter plus a short label for the prompt title.
"""
from __future__ import annotations

from discord import app_commands

from data.servants import ServantFilter

CLASS_CHOICES = [
    app_commands.Choice(name=n, value=v)
    for n, v in [
        ("Saber", "saber"), ("Archer", "archer"), ("Lancer", "lancer"),
        ("Rider", "rider"), ("Caster", "caster"), ("Assassin", "assassin"),
        ("Berserker", "berserker"), ("Ruler", "ruler"), ("Avenger", "avenger"),
        ("Moon Cancer", "mooncancer"), ("Alter Ego", "alterego"),
        ("Foreigner", "foreigner"), ("Pretender", "pretender"), ("Shielder", "shielder"),
    ]
]
RARITY_CHOICES = [app_commands.Choice(name=f"{r}-star", value=r) for r in (5, 4, 3, 2, 1)]
ATTRIBUTE_CHOICES = [
    app_commands.Choice(name=n, value=v)
    for n, v in [
        ("Sky", "sky"), ("Earth", "earth"), ("Man", "human"),
        ("Star", "star"), ("Beast", "beast"),
    ]
]
TRAIT_CHOICES = [
    app_commands.Choice(name=n, value=v)
    for n, v in [
        ("Dragon", "dragon"), ("Divine", "divine"), ("Divine Spirit", "divineSpirit"),
        ("King", "king"), ("Saberface", "saberface"), ("Demonic", "demonic"),
        ("Demonic Beast", "demonicBeastServant"), ("Child", "childServant"),
        ("Oni", "oni"), ("Fae", "fae"), ("Giant", "giant"),
        ("Knights of the Round", "knightsOfTheRound"), ("Arthurian", "arthur"),
        ("Greek Myth (M)", "greekMythologyMales"), ("Roman", "roman"),
        ("Genji/Minamoto", "genji"), ("Fairy Tale", "fairyTaleServant"),
        ("Lamia", "lamia"), ("Mechanical", "mechanical"),
        ("Argonaut", "associatedToTheArgo"), ("Brynhildr's Beloved", "brynhildsBeloved"),
        ("Summer", "summerModeServant"),
    ]
]

DESCRIBE = {
    "klass": "Only servants of this class",
    "rarity": "Only servants of this rarity",
    "attribute": "Only servants of this attribute",
    "trait": "Only servants with this trait/category",
}


# filter dimension -> the choice list, for turning stored values back into labels.
_LABEL_ORDER = (
    ("class_names", CLASS_CHOICES),
    ("rarities", RARITY_CHOICES),
    ("attributes", ATTRIBUTE_CHOICES),
    ("traits", TRAIT_CHOICES),
)


def label_for(filt: ServantFilter) -> str:
    """Human label for the Pool field, e.g. 'Saber/Archer * 5-star * Dragon' (choice
    order preserved; multiple values in a dimension joined with '/')."""
    parts = []
    for attr, choices in _LABEL_ORDER:
        values = getattr(filt, attr)
        if values:
            parts.append("/".join(c.name for c in choices if c.value in values))
    return " · ".join(parts)


def from_params(klass, rarity, attribute, trait):
    """(ServantFilter, label) from the optional single-choice params; (None, None) if
    none are set."""
    def one(choice):
        return frozenset([choice.value]) if choice else frozenset()

    filt = ServantFilter(
        class_names=one(klass),
        rarities=one(rarity),
        attributes=one(attribute),
        traits=one(trait),
    )
    if not filt.active:
        return None, None
    return filt, label_for(filt)
