"""Small Halloween event reward tables. Candy is spent as XP, not shop currency."""

from dataclasses import dataclass

HOST_ID = 502600  # Elisabeth Bathory (Halloween), already in the Atlas index
EVENT_SECONDS = 120
DROP_COOLDOWN = 60 * 60  # One shared automatic-event cooldown per channel.
# Mutually exclusive slices of one message roll: 1.5% total when the cooldown is ready.
# Keep the original 2:1 ratio of single events to Candy Spills.
DROP_CHANCES = {
    "grail_single": 0.005,
    "grail_box": 0.0025,
    "ember": 0.005,
    "ember_box": 0.0025,
}
TREAT_CANDY = 1
SPILL_CANDY = 1
SPILL_BONUS = 1
SPILL_EMOJIS = ("🍬", "🍭", "🍫")


@dataclass(frozen=True)
class LizLine:
    text: str
    emotion: str


SPILL_LINES = (
    LizLine("Master! I dropped my candy stash, help!", "worried"),
    LizLine("Master, grab those candies before Carmilla sees!", "embarrassed"),
    LizLine("My Halloween candy! Don't let it get away!", "upset"),
    LizLine("Quick, Master! Save the candy! My concert can wait!", "worried"),
)
TRICK_OR_TREAT_LINES = (
    LizLine("Trick or treat, my little fans? Choose wisely!", "happy"),
    LizLine("Master! Your Halloween idol has a surprise for you!", "happy"),
    LizLine("A trick or a treat? Go on, Master, pick one!", "pout"),
    LizLine("Candy or mischief? Let's hear it, my little fans!", "happy"),
)


@dataclass(frozen=True)
class TrickOutcome:
    candy: int
    flavor: str
    servant_id: int = HOST_ID
    qp_loss_percent: int = 0
    qp_loss_cap: int = 0
    emotion: str | None = None


# Four equally likely outcomes. Losses spend QP, never existing candy or grails.
TRICKS = (
    TrickOutcome(0, 'Liz hands you a ticket. "VIP admission isn\'t free, you know!"',
                 qp_loss_percent=1, qp_loss_cap=5000, emotion="pout"),
    TrickOutcome(0, 'Mecha Eli-chan points at Liz\'s broken confetti cannon. "You broke it. You pay for it."',
                 servant_id=1000400, qp_loss_percent=2, qp_loss_cap=10000),
    TrickOutcome(2, 'Liz beams. "An encore? For a fan like you, take two!"', emotion="happy"),
    TrickOutcome(4, 'Carmilla slips you four candies. "Not a word about this concert."',
                 servant_id=601700),
)
