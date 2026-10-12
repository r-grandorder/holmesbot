"""Small Halloween event reward tables. Candy is spent as XP, not shop currency."""

from dataclasses import dataclass

HOST_ID = 502600  # Elisabeth Bathory (Halloween), already in the Atlas index
EVENT_SECONDS = 120
TREAT_CANDY = 1
SPILL_CANDY = 1
SPILL_BONUS = 1
SPILL_EMOJIS = ("🍬", "🍭", "🍫")

SPILL_LINES = (
    "Master! I dropped my candy stash, help!",
    "Master, grab those candies before Carmilla sees!",
    "My Halloween candy! Don't let it get away!",
    "Quick, Master! Save the candy! My concert can wait!",
)
TRICK_OR_TREAT_LINES = (
    "Trick or treat, my little fans? Choose wisely!",
    "Master! Your Halloween idol has a surprise for you!",
    "A trick or a treat? Go on, Master, pick one!",
    "Candy or mischief? Let's hear it, my little fans!",
)


@dataclass(frozen=True)
class TrickOutcome:
    candy: int
    flavor: str
    servant_id: int = HOST_ID
    qp_loss: int = 0


# Four equally likely outcomes. Losses spend QP, never existing candy or grails.
TRICKS = (
    TrickOutcome(0, 'Liz hands you a ticket. "VIP admission isn\'t free, you know!"',
                 qp_loss=500),
    TrickOutcome(0, 'Mecha Eli-chan points at Liz\'s broken confetti cannon. "You broke it. You pay for it."',
                 servant_id=1000400, qp_loss=1000),
    TrickOutcome(2, 'Liz beams. "An encore? For a fan like you, take two!"'),
    TrickOutcome(2, 'Carmilla slips you two candies. "Not a word about this concert."',
                 servant_id=601700),
)
