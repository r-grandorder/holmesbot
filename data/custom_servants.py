"""Custom-servant config helpers: validation shared by the bot and the web editor.

A custom servant definition (stored as JSON in custom_servants.def_json, and mirrored by the
hand-authored entries in data/custom_servants.json) looks like:

    {
      "id": -9001,                      # ALWAYS negative -- see _id_error
      "name": "Gudako: Maximum Overdrive",
      "className": "caster",            # must be a class the battle engine knows
      "rarity": 5,
      "art":  {"0": "https://.../art.png"},    # ascension key -> URL; "0" is required
      "face": "https://.../face.png",          # square portrait (thumbnails, /duel banner)
      "gender": "female",
      "attribute": "human",
      "aliases": ["gudako"],
      "traits": [],
      "summon_line": "GACHA GACHA ...",
      "summon_weight": 0.025,           # relative summon odds; its own gacha bucket
      "wishable": false
    }

Validation mirrors data/raids.validate_raid_def: return a list of human-readable error strings,
empty when the definition is good. Collision checks against the live servant index are optional
so the pure-schema checks stay usable without one.
"""
from __future__ import annotations

from data.autobattle import CLASS_NAMES

# Ascension key every custom unit must supply: the base art shown by /summon and the
# battle scene. Custom units are summon-only, so extra ascensions are optional flavour.
BASE_ART_KEY = "0"

# Fields a custom entry may carry. Anything else is rejected rather than silently dropped, so a
# typo'd key ("classname", "weight") fails loudly in the editor instead of quietly doing nothing.
ALLOWED_KEYS = frozenset({
    "id", "name", "className", "rarity", "art", "face", "figure", "gender", "attribute",
    "aliases", "traits", "skills", "summon_line", "summon_weight", "wishable", "cv",
    "atk_base", "atk_max", "hp_base", "hp_max", "custom",
})


def _pos_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _id_error(value) -> "str | None":
    """Custom ids live in the negative space. ServantIndex.load drops any earlier servant with
    a matching id when it loads customs, so a positive id would silently replace a real Atlas
    servant -- the pool would look fine and one real servant would just vanish."""
    if not isinstance(value, int) or isinstance(value, bool):
        return "id must be an integer"
    if value >= 0:
        return "id must be negative (the custom id space); positive ids shadow real servants"
    return None


def _str_list_error(value, label: str) -> "str | None":
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return f"{label} must be a list of strings"
    return None


def validate_custom_servant(defn: dict, index=None) -> "list[str]":
    """Schema-check a custom servant definition; returns error strings (empty = valid).

    Pass `index` (a ServantIndex) to also check name/alias collisions against the live pool.
    That matters because a custom unit's NAME joins the guess-matching corpus even though the
    unit itself is never a guess target: ServantIndex.pick excludes customs, but
    resembles_servant and spaced_names iterate every servant, and spaced_names feeds the
    token-subset uniqueness rule in matching.is_correct_guess. A colliding name can therefore
    make a legitimate guess for a REAL servant ambiguous.
    """
    errors: list[str] = []
    if not isinstance(defn, dict):
        return ["definition must be an object"]

    unknown = sorted(set(defn) - ALLOWED_KEYS)
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")

    err = _id_error(defn.get("id"))
    if err:
        errors.append(err)

    name = defn.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")

    cls = defn.get("className")
    if not isinstance(cls, str) or cls.strip().lower() not in CLASS_NAMES:
        errors.append(f"className must be one of: {', '.join(CLASS_NAMES)}")

    rarity = defn.get("rarity")
    if not isinstance(rarity, int) or isinstance(rarity, bool) or not (1 <= rarity <= 5):
        errors.append("rarity must be an integer 1-5")

    art = defn.get("art")
    if not isinstance(art, dict) or not art.get(BASE_ART_KEY):
        errors.append(f'art must be an object with a non-empty "{BASE_ART_KEY}" entry')
    elif not all(isinstance(k, str) and isinstance(v, str) and v for k, v in art.items()):
        errors.append("art keys and URLs must all be non-empty strings")

    face = defn.get("face")
    if face is not None and (not isinstance(face, str) or not face.strip()):
        errors.append("face must be a non-empty string when set")

    weight = defn.get("summon_weight", 1.0)
    if not _pos_num(weight):
        errors.append("summon_weight must be a positive number")

    if defn.get("wishable") is not None and not isinstance(defn["wishable"], bool):
        errors.append("wishable must be true or false")

    for key in ("aliases", "traits"):
        err = _str_list_error(defn.get(key), key)
        if err:
            errors.append(err)

    for key in ("atk_base", "atk_max", "hp_base", "hp_max"):
        v = defn.get(key)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            errors.append(f"{key} must be a non-negative integer")

    if index is not None and isinstance(name, str) and name.strip():
        errors.extend(_collision_errors(defn, index))
    return errors


def _collision_errors(defn: dict, index) -> "list[str]":
    """Name/alias collisions against every OTHER servant in the pool, compared the way the
    guess games compare them (accent-stripped, punctuation-free, lowercase)."""
    from data import matching

    out: list[str] = []
    own_id = defn.get("id")
    taken: dict[str, str] = {}
    for s in index.all():
        if s.id == own_id:
            continue  # editing an existing custom unit: it may keep its own name
        taken.setdefault(matching.normalize(s.name), s.name)

    norm_name = matching.normalize(defn["name"])
    if norm_name in taken:
        out.append(
            f"name collides with the existing servant {taken[norm_name]!r} -- "
            "a duplicate name makes real guesses ambiguous"
        )
    for alias in defn.get("aliases") or []:
        clash = taken.get(matching.normalize(alias))
        if clash:
            out.append(f"alias {alias!r} collides with the existing servant {clash!r}")
    return out


def to_index_item(defn: dict) -> dict:
    """A stored definition as ServantIndex._from_item expects it. `custom` is forced on so a
    row can never accidentally register as a normal (guessable) servant."""
    return {**defn, "custom": True}
