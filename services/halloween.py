"""Atomic candy claims, including once-per-player and group completion rewards."""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass

from data import halloween as H


@dataclass(frozen=True)
class CandyClaim:
    status: str
    candy: int = 0
    grails: int = 0
    balance: int = 0
    claimed_slots: tuple[int, ...] = ()
    complete: bool = False
    bonus: int = 0
    flavor: str = ""
    servant_id: int = H.HOST_ID
    qp_penalty: int = 0
    qp_lost: int = 0
    qp_balance: int | None = None


class HalloweenService:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def balance(self, guild_id: int, user_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT candy FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        ) or 0

    async def create_event(
        self, guild_id: int, channel_id: int, kind: str, *, slots: int = 5,
        grails: int = 0, expires_at: float | None = None,
    ) -> dict:
        if kind not in ("trick_or_treat", "candy_spill"):
            raise ValueError("Unknown Halloween event")
        if kind == "trick_or_treat":
            slots = 1
        if not 1 <= slots <= 8 or grails not in (0, 1):
            raise ValueError("Invalid Halloween reward configuration")
        now = time.time()
        event = dict(id=uuid.uuid4().hex, guild_id=guild_id, channel_id=channel_id,
                     kind=kind, slots=slots, grails=grails,
                     expires_at=expires_at if expires_at is not None else now + H.EVENT_SECONDS)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # A short claim ledger prevents duplicate payouts; old claims cascade away.
                await conn.execute("DELETE FROM halloween_events WHERE expires_at < $1", now - 86400)
                await conn.execute(
                    "INSERT INTO halloween_events (id, guild_id, channel_id, kind, slots, grails, expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    event["id"], guild_id, channel_id, kind, slots, grails, event["expires_at"],
                )
        return event

    async def claim(
        self, event_id: str, guild_id: int, channel_id: int, user_id: int,
        *, action: str = "collect", slot: int = 0,
    ) -> CandyClaim:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                event = await conn.fetchrow("SELECT * FROM halloween_events WHERE id = $1", event_id)
                if (event is None or event["guild_id"] != guild_id
                        or event["channel_id"] != channel_id or time.time() >= event["expires_at"]):
                    return CandyClaim("expired")
                if event["kind"] == "trick_or_treat":
                    if action not in ("trick", "treat") or slot != 0:
                        return CandyClaim("invalid")
                elif action != "collect" or not 0 <= slot < event["slots"]:
                    return CandyClaim("invalid")
                claims = await conn.fetch(
                    "SELECT user_id, slot FROM halloween_claims WHERE event_id = $1", event_id,
                )
                if any(c["user_id"] == user_id for c in claims):
                    return CandyClaim("duplicate")
                if any(c["slot"] == slot for c in claims):
                    return CandyClaim("taken")
                servant_id, qp_penalty = H.HOST_ID, 0
                if action == "trick":
                    outcome = random.choice(H.TRICKS)
                    candy, flavor = outcome.candy, outcome.flavor
                    servant_id, qp_penalty = outcome.servant_id, outcome.qp_loss
                elif action == "treat":
                    candy, flavor = H.TREAT_CANDY, 'Liz hands you a Halloween candy. "A special treat for my little fan!"'
                else:
                    candy, flavor = H.SPILL_CANDY, "You catch one of the candies Liz meant to throw during her encore."
                await conn.execute(
                    "INSERT INTO halloween_claims (event_id, user_id, slot, candy) VALUES ($1, $2, $3, $4)",
                    event_id, user_id, slot, candy,
                )
                qp_lost, qp_balance = 0, None
                if qp_penalty:
                    # Use this transaction's connection so a failed reward also refunds
                    # the penalty. The unique claim prevents duplicate charges on retries.
                    qp_balance = await conn.fetchval(
                        "SELECT balance FROM scores WHERE guild_id = $1 AND user_id = $2",
                        guild_id, user_id,
                    ) or 0
                    qp_lost = min(qp_penalty, qp_balance)
                    if qp_lost:
                        await conn.execute(
                            "UPDATE scores SET balance = balance - $3, updated_at = CURRENT_TIMESTAMP "
                            "WHERE guild_id = $1 AND user_id = $2",
                            guild_id, user_id, qp_lost,
                        )
                    qp_balance -= qp_lost
                grails = event["grails"]
                balance = await conn.fetchval(
                    "INSERT INTO grail_balance (guild_id, user_id, candy, balance) VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (guild_id, user_id) DO UPDATE "
                    "SET candy = candy + $3, balance = balance + $4 RETURNING candy",
                    guild_id, user_id, candy, grails,
                )
                claimed_slots = tuple(sorted([c["slot"] for c in claims] + [slot]))
                complete = len(claimed_slots) == event["slots"]
                bonus = H.SPILL_BONUS if complete and event["kind"] == "candy_spill" else 0
                if bonus:
                    await conn.execute(
                        "UPDATE grail_balance SET candy = candy + $3 WHERE guild_id = $1 "
                        "AND user_id IN (SELECT user_id FROM halloween_claims WHERE event_id = $2)",
                        guild_id, event_id, bonus,
                    )
                    balance += bonus
                return CandyClaim(
                    status="ok", candy=candy, grails=grails, balance=balance,
                    claimed_slots=claimed_slots, complete=complete, bonus=bonus, flavor=flavor,
                    servant_id=servant_id, qp_penalty=qp_penalty, qp_lost=qp_lost, qp_balance=qp_balance,
                )
