"""The bot's MC voice: Sherlock Holmes (FGO servant 900500).

DORMANT for now -- the @mention persona is disabled (no longer wired into
cogs/chat_guess.py) to keep the launch scope tight. The lines and the S3 faces
(persona/holmes/v1/) are kept ready; re-enable by routing tags back through here.

Keyword-bucketed, semi-responsive, rotating lines -- no LLM, so it is spoiler-proof
by construction (it can never leak a round's answer). Grounded in his FGO voice
lines and the legacy blackjack-dealer persona: a smug-but-warm consulting detective
who frames everything as deduction and drops "Elementary" / "The game is afoot."
Faces live on S3 at persona/holmes/<version>/. Use {player} sparingly -- it should
land now and then, not every line.
"""
from __future__ import annotations

import random

NAME = "Sherlock Holmes"
SERVANT_ID = 900500

FACES = ("angry", "happy", "neutral", "suspicious", "worried")
FACE_VERSION = "v1"


def face_path(emotion: str) -> str:
    if emotion not in FACES:
        emotion = "neutral"
    return f"persona/holmes/{FACE_VERSION}/{emotion}.png"


# Each bucket: (emotion, [lines]). {player} -> the tagger's display name (used rarely).
_BUCKETS: dict[str, tuple[str, list[str]]] = {
    "greeting": (
        "happy",
        [
            "Ah, {player}. Have you business with me, or are you merely making observations?",
            "An assistant arrives. Good. Sitting around won't bring new clients.",
            "Hm. You have my attention. Do make it interesting.",
            "The game is afoot, is it? Then let us begin.",
        ],
    ),
    "praise": (
        "happy",
        [
            "Elementary. But I shall accept the compliment.",
            "Naturally. I chose to be this way.",
            "Hahaha. Watson said much the same. You are in good company.",
            "Kind of you. Do try not to let it cloud your deductions.",
        ],
    ),
    "trash": (
        "angry",
        [
            "There is nothing more deceptive than an obvious insult.",
            "You see, but you do not observe.",
            "Curious. I deduce you have simply not won a round yet.",
            "Noted. The remark reveals far more about you than about me.",
        ],
    ),
    "who": (
        "neutral",
        [
            "A consulting detective. Think of me as the one who sets the puzzles.",
            "Sherlock Holmes. If you expected a hero, my apologies. A detective will have to do.",
            "The proprietor of this little parlor of deductions. And you are?",
        ],
    ),
    "real": (
        "suspicious",
        [
            "Real enough to observe that you are stalling. Open a case.",
            "A fair question. The answer, as always, is in the details.",
            "Does it matter? The deductions are quite real.",
        ],
    ),
    "spoiler": (
        "suspicious",
        [
            "A detective deals in deductions, not handouts. Tag me for a hint and earn it.",
            "Tut tut. I set the puzzles; I do not solve them for you.",
            "Nice try, {player}. I observed that maneuver coming a mile off.",
            "The answer? You wound me. Half the pleasure is in the deduction.",
        ],
    ),
    "frustration": (
        "worried",
        [
            "Difficulty is merely detail you have not yet observed.",
            "Patience, {player}. You will become a fine detective, one step at a time.",
            "A troubling case, I admit. But not an unsolvable one.",
            "Breathe. Even Watson needed a moment now and again.",
        ],
    ),
    "fandom": (
        "happy",
        [
            "Sentiment is data too, I suppose. Now, shall we have a case?",
            "Passions are illuminating. Mind you channel them into a guess.",
        ],
    ),
    "help": (
        "neutral",
        [
            "A simple enough case: I present a fragment of a Servant, and you name them. "
            "Open one with /guessservant, /guessshadow, or /guessvoice, then state your "
            "conclusion in chat. Tag me mid-investigation for a hint, or to concede. Elementary.",
        ],
    ),
    "scores": (
        "neutral",
        [
            "The standings are a matter of record: /leaderboard. Your own QP: /qp. "
            "Observe them closely.",
        ],
    ),
    "generic": (
        "neutral",
        [
            "Interesting.",
            "Fascinating. Though I see no case before me at present.",
            "I am a detective, not a conversationalist. Bring me a mystery.",
            "Merely looking with your eyes will not suffice. Shall we put that to the test?",
            "The game is not yet afoot. Open one with /guessservant, and we shall see what you observe.",
            "Curious. State your business.",
        ],
    ),
}

# (bucket, keywords), checked in order; first match wins. Short/ambiguous words are
# space-padded so they only match as whole words.
_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scores", ("leaderboard", "standings", "scores", "ranking", "my qp", "my points", "how much qp", "richest")),
    ("help", ("how do i", "how to play", "how does this", " help", "rules", "what do you do", "what can you do", "get started", "commands", "play a game", "start a game")),
    ("spoiler", ("the answer", "who is it", "who's it", "just tell me", "give me the answer", "what is it", "tell me who")),
    ("who", ("who are you", "what are you", "who is this", "introduce yourself")),
    ("real", ("are you real", "are you a bot", "are you human", "are you ai", "are you a person", "are you alive")),
    ("praise", ("good bot", "great bot", "best bot", "thank", "thanks", "love you", "love ya", "well done", "good job", "amazing", "awesome", "you rock", "good boy")),
    ("trash", ("bad bot", "dumb", "stupid", "trash", "hate you", "useless", "you suck", "worst", "shut up")),
    ("frustration", ("this is hard", "too hard", "i can't", "i cant", "impossible", "i give up", "so hard", "no idea", "i'm stuck", "im stuck")),
    ("fandom", ("i love fgo", "love this game", "best servant", "waifu", "husbando", "best girl")),
    ("greeting", (" hi ", " hey ", " yo ", " sup ", "hello", "howdy", "greetings", "good morning", "good evening")),
)


def respond(text: str, player: str = "") -> tuple[str, str]:
    """Return (line, emotion) for a message that tagged the bot."""
    t = f" {text.lower()} "
    bucket = "generic"
    for name, keys in _ROUTES:
        if any(k in t for k in keys):
            bucket = name
            break
    emotion, lines = _BUCKETS[bucket]
    line = random.choice(lines).replace("{player}", player or "you")
    return line, emotion
