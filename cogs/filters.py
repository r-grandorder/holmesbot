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


def from_params(klass, rarity, attribute, trait):
    """(ServantFilter, title-label) from the optional Choice params; (None, None) if
    none are set."""
    filt = ServantFilter(
        class_name=klass.value if klass else None,
        rarity=rarity.value if rarity else None,
        attribute=attribute.value if attribute else None,
        trait=trait.value if trait else None,
    )
    if not filt.active:
        return None, None
    parts = [c.name for c in (klass, rarity, attribute, trait) if c]
    return filt, " · ".join(parts)
