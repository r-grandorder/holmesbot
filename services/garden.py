"""The garden: plant heights, watering, and Quality Fertilizer.

All the state lives in plant_heights / water_rewards / mulch_effects plus a `fertilizer` column
on grail_balance. Everything a single /water touches happens in one transaction, so a growth
can't land without its cooldown stamp and a QP payout can't be claimed twice.
"""
from __future__ import annotations

import datetime as dt

from data import garden


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class GardenService:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def height(self, guild_id: int, user_id: int) -> dict:
        """height_mm / times_watered / next_growth_at for a user; the starting sprout if they
        have no row. next_growth_at is None when they can be watered for growth right now."""
        row = await self.pool.fetchrow(
            "SELECT height_mm, times_watered, last_growth_at FROM plant_heights "
            "WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        if row is None:
            return {"height_mm": garden.STARTING_HEIGHT_MM, "times_watered": 0,
                    "next_growth_at": None}
        ready = _next_growth(row["last_growth_at"])
        return {
            "height_mm": float(row["height_mm"]),
            "times_watered": int(row["times_watered"]),
            "next_growth_at": ready if ready and ready > _utcnow() else None,
        }

    async def tallest(self, guild_id: int, limit: int = 10) -> "list":
        return await self.pool.fetch(
            "SELECT user_id, height_mm, times_watered FROM plant_heights "
            "WHERE guild_id = $1 AND height_mm > $2 ORDER BY height_mm DESC LIMIT $3",
            guild_id,
            garden.STARTING_HEIGHT_MM,
            limit,
        )

    async def active_mulch(self, guild_id: int, user_id: int) -> "dict | None":
        """The live Quality Fertilizer on a user, or None. Expired rows are simply ignored
        rather than swept: they're overwritten by the next /mulch on that user."""
        row = await self.pool.fetchrow(
            "SELECT multiplier, expires_at, applied_by FROM mulch_effects "
            "WHERE guild_id = $1 AND user_id = $2 AND expires_at > $3",
            guild_id,
            user_id,
            _utcnow(),
        )
        if row is None:
            return None
        return {"multiplier": float(row["multiplier"]), "expires_at": row["expires_at"],
                "applied_by": row["applied_by"]}

    async def water(
        self, guild_id: int, waterer_id: int, target_id: int, *, qp_cooldown_hours: float
    ) -> dict:
        """Water `target_id`. Returns what happened:

            grew, growth_mm, height_mm, times_watered, multiplier, mulch_expires, reward_due

        The growth cooldown is on the TARGET, so watering someone who grew recently still
        counts (and still pays) but grows them by nothing. `reward_due` is the WATERER's
        separate QP cooldown: it is stamped here, inside the transaction, so two rapid calls
        can't both claim it -- but the QP itself is added by the caller through ScoringService,
        which keeps the money in one place. A crash between the two loses a payout rather than
        paying twice, which is the safe direction.
        """
        now = _utcnow()
        growth_threshold = now - dt.timedelta(hours=garden.GROWTH_COOLDOWN_HOURS)
        reward_threshold = now - dt.timedelta(hours=qp_cooldown_hours)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT height_mm, times_watered, last_growth_at FROM plant_heights "
                    "WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    target_id,
                )
                height = float(row["height_mm"]) if row else garden.STARTING_HEIGHT_MM
                times = int(row["times_watered"]) if row else 0
                last_growth = row["last_growth_at"] if row else None

                mulch = await conn.fetchrow(
                    "SELECT multiplier, expires_at FROM mulch_effects "
                    "WHERE guild_id = $1 AND user_id = $2 AND expires_at > $3",
                    guild_id,
                    target_id,
                    now,
                )
                multiplier = float(mulch["multiplier"]) if mulch else 1.0

                # Timestamps are stored in the same 'YYYY-MM-DD HH:MM:SS' UTC form CURRENT_TIMESTAMP
                # uses, so a plain string comparison against the threshold is chronological.
                can_grow = last_growth is None or str(last_growth) <= _stamp(growth_threshold)
                grew, gained = False, 0.0
                if can_grow:
                    gained = garden.growth_amount(height) * multiplier
                    height += gained
                    grew = True
                times += 1

                await conn.execute(
                    "INSERT INTO plant_heights (guild_id, user_id, height_mm, times_watered, "
                    "last_growth_at) VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (guild_id, user_id) DO UPDATE SET height_mm = $3, "
                    "times_watered = $4, last_growth_at = COALESCE($5, last_growth_at)",
                    guild_id,
                    target_id,
                    height,
                    times,
                    now if grew else None,
                )

                # The waterer's QP cooldown. db.py is a single connection and acquire() holds
                # its lock for the whole transaction, so nothing can interleave between this
                # read and the write below -- no conditional-update trick needed.
                last_reward = await conn.fetchval(
                    "SELECT last_reward_at FROM water_rewards WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    waterer_id,
                )
                reward_due = last_reward is None or str(last_reward) <= _stamp(reward_threshold)
                if reward_due:
                    await conn.execute(
                        "INSERT INTO water_rewards (guild_id, user_id, last_reward_at) "
                        "VALUES ($1, $2, $3) "
                        "ON CONFLICT (guild_id, user_id) DO UPDATE SET last_reward_at = $3",
                        guild_id,
                        waterer_id,
                        now,
                    )

                return {
                    "grew": grew,
                    "growth_mm": gained,
                    "height_mm": height,
                    "times_watered": times,
                    "multiplier": multiplier,
                    "mulch_expires": mulch["expires_at"] if mulch else None,
                    "reward_due": reward_due,
                    # When this plant can next grow. On a successful water that's a full
                    # cooldown from now; on a refused one it's a cooldown from the growth that
                    # blocked it, so the embed can show exactly how long is left.
                    "next_growth_at": (
                        now + dt.timedelta(hours=garden.GROWTH_COOLDOWN_HOURS)
                        if grew else _next_growth(last_growth)
                    ),
                }

    async def reset_cooldowns(self, guild_id: int, user_id: int) -> "dict[str, bool]":
        """Clear this user's garden timers: the growth cooldown on their own plant, and their
        watering payout cooldown. Height and watering count are deliberately untouched -- this
        resets timing, not progress, so a reset can't be used to farm the leaderboard."""
        grown = await self.pool.fetchrow(
            "UPDATE plant_heights SET last_growth_at = NULL "
            "WHERE guild_id = $1 AND user_id = $2 AND last_growth_at IS NOT NULL RETURNING 1",
            guild_id,
            user_id,
        )
        paid = await self.pool.fetchrow(
            "DELETE FROM water_rewards WHERE guild_id = $1 AND user_id = $2 RETURNING 1",
            guild_id,
            user_id,
        )
        return {"growth": grown is not None, "payout": paid is not None}

    # --- Quality Fertilizer ---------------------------------------------------------------
    async def fertilizer(self, guild_id: int, user_id: int) -> int:
        val = await self.pool.fetchval(
            "SELECT fertilizer FROM grail_balance WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return int(val or 0)

    async def grant_fertilizer(self, guild_id: int, user_id: int, n: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO grail_balance (guild_id, user_id, fertilizer) VALUES ($1, $2, MAX(0, $3)) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET fertilizer = MAX(0, fertilizer + $3) "
            "RETURNING fertilizer",
            guild_id,
            user_id,
            n,
        )

    async def apply_mulch(
        self, guild_id: int, giver_id: int, target_id: int, *, multiplier: float, hours: float
    ) -> "dict | None":
        """Spend one of giver's Quality Fertilizer to mulch `target_id`. Returns the new effect,
        or None if they had none. Re-mulching REFRESHES rather than stacks, so the buff can't be
        piled up by burning stock."""
        now = _utcnow()
        expires = now + dt.timedelta(hours=hours)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                spent = await conn.fetchrow(
                    "UPDATE grail_balance SET fertilizer = fertilizer - 1 "
                    "WHERE guild_id = $1 AND user_id = $2 AND fertilizer >= 1 RETURNING fertilizer",
                    guild_id,
                    giver_id,
                )
                if spent is None:
                    return None
                await conn.execute(
                    "INSERT INTO mulch_effects (guild_id, user_id, multiplier, expires_at, "
                    "applied_by) VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (guild_id, user_id) DO UPDATE SET multiplier = $3, "
                    "expires_at = $4, applied_by = $5",
                    guild_id,
                    target_id,
                    multiplier,
                    expires,
                    giver_id,
                )
                return {"multiplier": multiplier, "expires_at": expires,
                        "remaining": int(spent["fertilizer"])}


def _next_growth(last_growth_at) -> "dt.datetime | None":
    """When a plant last grown at `last_growth_at` becomes waterable again."""
    prev = _parse(last_growth_at)
    return prev + dt.timedelta(hours=garden.GROWTH_COOLDOWN_HOURS) if prev else None


def _parse(value) -> "dt.datetime | None":
    """A stored timestamp back into an aware UTC datetime. The inverse of _stamp."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def _stamp(when: dt.datetime) -> str:
    """A datetime in the exact text form db.py stores (UTC, 'YYYY-MM-DD HH:MM:SS'), so it can be
    compared against stored timestamps as a string."""
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return when.strftime("%Y-%m-%d %H:%M:%S")
