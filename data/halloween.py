"""Small Halloween event reward tables. Candy is spent as XP, not shop currency."""

from dataclasses import dataclass

HOST_ID = 502600  # Elisabeth Bathory (Halloween), already in the Atlas index
EVENT_SECONDS = 120
TREAT_CANDY = 1
SPILL_CANDY = 1
SPILL_BONUS = 1

@dataclass(frozen=True)
class TrickOutcome:
    candy: int
    flavor: str
    servant_id: int = HOST_ID
    qp_loss: int = 0


# Four equally likely outcomes. Losses spend QP, never existing candy or grails.
TRICKS = (
    TrickOutcome(0, 'Liz presents a concert ticket. "Surprise! That was the VIP entrance. Admission, please!"',
                 qp_loss=500),
    TrickOutcome(0, 'Mecha Eli-chan catches you beside a smoking confetti cannon. "You touched Elisabeth\'s stage controls. The repair bill is yours."',
                 servant_id=1000400, qp_loss=1000),
    TrickOutcome(2, 'Liz mistakes your wave for an encore request. "Such a devoted fan! Here, take two!"'),
    TrickOutcome(2, 'Carmilla presses two candies into your hand. "In exchange, you never mention this concert to me again."',
                 servant_id=601700),
)
