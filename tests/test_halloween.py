from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from discord import app_commands
import discord
from discord.ui.view import ViewStore

from config import Config
from cogs.contracts import ContractsCog
from cogs.halloween import CandyEventView, HalloweenCog
from data import halloween as H
from data.servants import Servant, ServantIndex
from db import Database
from scripts.sync_atlas import _growth_lines
from services.contracts import ContractService
from services.halloween import HalloweenService

ROOT = Path(__file__).resolve().parents[1]


def config(**values):
    env = {"DISCORD_BOT_TOKEN": "test", "DISCORD_APPLICATION_ID": "999",
           "DATABASE_URL": "sqlite::memory:", "CONTRACT_WHITELIST": "all", **values}
    with patch.dict(os.environ, env, clear=True):
        return Config.from_env()


def interaction(user_id=100):
    return SimpleNamespace(
        guild_id=1, channel_id=2, user=SimpleNamespace(id=user_id, display_name="Tester"),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class HalloweenConfigTests(unittest.TestCase):
    def test_default_and_explicit_off(self):
        for value in ("", "false", "off", "0", "invalid"):
            cfg = config(HALLOWEEN_WHITELIST=value)
            self.assertFalse(cfg.halloween_open or cfg.halloween_whitelist)
            self.assertFalse(cfg.halloween_allowed(100))
            self.assertFalse(cfg.halloween_active())
            self.assertFalse(cfg.halloween_drops_active())

    def test_random_event_switch_preserves_manual_access(self):
        self.assertTrue(config(HALLOWEEN_WHITELIST="100").halloween_drops_active())
        for value in ("", "false", "off", "0", "true", "1", "yes", "on", "enabled", " TRUE "):
            with self.subTest(value=value):
                cfg = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_RANDOM_EVENTS=value)
                enabled = value.strip().lower() in ("true", "1", "yes", "on", "enabled")
                self.assertEqual(cfg.halloween_drops_active(), enabled)
                self.assertTrue(cfg.halloween_active())
                self.assertTrue(cfg.halloween_allowed(100))
                self.assertFalse(cfg.halloween_allowed(999))

    def test_testers_need_both_whitelists(self):
        cfg = config(HALLOWEEN_WHITELIST="100, 200 300", CONTRACT_WHITELIST="100,200")
        self.assertTrue(cfg.halloween_allowed(100))
        self.assertTrue(cfg.halloween_allowed(200))
        self.assertFalse(cfg.halloween_allowed(300))
        self.assertFalse(cfg.halloween_allowed(400))
        self.assertTrue(config(HALLOWEEN_WHITELIST="all").halloween_allowed(400))
        self.assertFalse(config(HALLOWEEN_WHITELIST="all", CONTRACT_WHITELIST="").halloween_allowed(100))

    def test_schedule_boundaries_and_feeding_after_end(self):
        cfg = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_START_AT="2026-10-20T00:00:00-07:00",
                     HALLOWEEN_END_AT="2026-11-01T00:00:00-07:00")
        self.assertFalse(cfg.halloween_active(cfg.halloween_start_at - dt.timedelta(seconds=1)))
        self.assertFalse(cfg.halloween_drops_active(cfg.halloween_start_at - dt.timedelta(seconds=1)))
        self.assertTrue(cfg.halloween_active(cfg.halloween_start_at))
        self.assertTrue(cfg.halloween_drops_active(cfg.halloween_start_at))
        self.assertTrue(cfg.halloween_active(cfg.halloween_end_at - dt.timedelta(seconds=1)))
        self.assertFalse(cfg.halloween_active(cfg.halloween_end_at))
        self.assertFalse(cfg.halloween_drops_active(cfg.halloween_end_at))
        self.assertTrue(cfg.halloween_allowed(100))

    def test_invalid_dates_fail_with_clear_error(self):
        for value in ("tomorrow", "2026-10-20", "2026-10-20T00:00:00"):
            with self.assertRaisesRegex(RuntimeError, "HALLOWEEN_START_AT"):
                config(HALLOWEEN_START_AT=value)
        with self.assertRaisesRegex(RuntimeError, "must be after"):
            config(HALLOWEEN_START_AT="2026-11-01T00:00:00Z", HALLOWEEN_END_AT="2026-10-01T00:00:00Z")


class GrowthDialogueTests(unittest.TestCase):
    def test_only_base_form_level_up_lines_are_used_and_cleaned(self):
        line = {"condType": "svtLevel", "conds": [{"condType": "levelUp"}],
                "subtitle": "[id 502600_0_G010,502600_0_G011]Thanks. [delay 0.1] I can still get stronger, Master!"}
        record = {"profile": {"voices": [
            {"type": "groeth", "voicePrefix": 0, "voiceLines": [line, line,
                {"condType": "svtLimit", "subtitle": "Ascension spoiler"},
                {"conds": [{"condType": "levelUp"}], "subtitle": "[MaterialOnly Alice: ]Another [wait 1] reaction."}]},
            {"type": "groeth", "voicePrefix": 1, "voiceLines": [{**line, "subtitle": "Different form"}]},
            {"type": "battle", "voiceLines": [line]},
        ]}}
        self.assertEqual(_growth_lines(record), ["Thanks. I can still get stronger, Master!", "Alice: Another reaction."])
        self.assertEqual(_growth_lines({"profile": None}), [])

    def test_index_loads_dialogue_and_old_records_still_load(self):
        raw = dict(id=502600, name="Liz", art={"1": "https://example.com/art.png"},
                   face="https://example.com/face.png", growth_lines=["Thanks!"])
        self.assertEqual(ServantIndex.item_to_servant(raw).growth_lines, ("Thanks!",))
        del raw["growth_lines"]
        self.assertEqual(ServantIndex.item_to_servant(raw).growth_lines, ())


class HalloweenFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database("sqlite::memory:")
        await self.db.connect()
        for migration in sorted((ROOT / "database/migrations").glob("*.sql")):
            await self.db._conn.executescript(migration.read_text().split("-- migrate:down")[0])
        self.contracts = ContractService(self.db.pool)
        self.service = HalloweenService(self.db.pool)
        self.servant = Servant(id=502600, name="Elisabeth Bathory (Halloween)", class_name="caster", rarity=4,
                              art={"1": "https://example.com/liz.png"}, figure={},
                              face="https://example.com/liz-face.png", growth_lines=("Thanks!",))
        guests = [Servant(id=sid, name=name, class_name=cls, rarity=4,
                         art={"1": f"https://example.com/{sid}.png"}, figure={},
                         face=f"https://example.com/{sid}-face.png")
                  for sid, name, cls in ((601700, "Carmilla", "assassin"),
                                         (1000400, "Mecha Eli-chan", "alterEgo"))]
        self.bot = SimpleNamespace(
            config=config(HALLOWEEN_WHITELIST="100,200,300,400,500"), db=self.db, contracts=self.contracts,
            servants=ServantIndex([self.servant, *guests]),
            restrictions=SimpleNamespace(build_allow=AsyncMock(return_value=lambda sid, key: True)),
            guild_config=SimpleNamespace(is_event_channel_allowed=AsyncMock(return_value=True)),
        )
        self.cog = HalloweenCog(self.bot)

    async def asyncTearDown(self):
        for view in tuple(self.cog._views):
            view.stop()
        await self.db.close()

    def assert_liz_portrait(self, embed, files, emotion):
        self.assertEqual(embed.thumbnail.url, f"attachment://halloween-liz-{emotion}.png")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, f"halloween-liz-{emotion}.png")
        self.assertEqual(files[0].fp.getvalue(), (ROOT / "assets/halloween/liz" / f"{emotion}.png").read_bytes())

    async def seed_candy(self, uid=100, amount=20):
        await self.db.pool.execute(
            "INSERT INTO grail_balance (guild_id, user_id, candy) VALUES (1, $1, $2) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET candy = $2", uid, amount,
        )

    async def seed_qp(self, uid=100, amount=5000):
        await self.db.pool.execute(
            "INSERT INTO scores (guild_id, user_id, balance, points) VALUES (1, $1, $2, 10000) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET balance = $2", uid, amount,
        )

    async def qp_balance(self, uid=100):
        return await self.db.pool.fetchval(
            "SELECT balance FROM scores WHERE guild_id = 1 AND user_id = $1", uid,
        ) or 0

    async def test_trick_or_treat_allows_each_player_once_without_a_participant_cap(self):
        event = await self.service.create_event(1, 2, "trick_or_treat", grails=1)
        users = list(range(100, 120))
        results = await asyncio.gather(*(
            self.service.claim(event["id"], 1, 2, uid, action="treat") for uid in users + users
        ))
        self.assertEqual(sum(r.status == "ok" for r in results), len(users))
        self.assertEqual(sum(r.status == "duplicate" for r in results), len(users))
        self.assertTrue(all(not r.complete and not r.bonus for r in results))
        self.assertEqual(await self.db.pool.fetchval("SELECT SUM(candy) FROM grail_balance"), len(users))
        self.assertEqual(await self.db.pool.fetchval("SELECT SUM(balance) FROM grail_balance"), len(users))
        for uid in users:
            self.assertEqual(await self.service.balance(1, uid), 1)
        with patch("services.halloween.time.time", return_value=event["expires_at"]):
            late = await self.service.claim(event["id"], 1, 2, 999, action="treat")
        self.assertEqual(late.status, "expired")
        self.assertEqual(await self.service.balance(1, 999), 0)

    async def test_spill_duplicate_player_or_slot_cannot_claim_again(self):
        event = await self.service.create_event(1, 2, "candy_spill")
        first = await self.service.claim(event["id"], 1, 2, 100, slot=0)
        self.assertEqual(first.candy, 1)
        self.assertEqual((await self.service.claim(event["id"], 1, 2, 100, slot=1)).status, "duplicate")
        self.assertEqual((await self.service.claim(event["id"], 1, 2, 200, slot=0)).status, "taken")
        self.assertEqual(await self.service.balance(1, 100), first.candy)
        self.assertEqual(await self.service.balance(1, 200), 0)

    async def test_each_trick_charges_only_its_qp_penalty_and_never_allows_a_retry(self):
        for outcome in H.TRICKS:
            with self.subTest(outcome=outcome):
                await self.seed_candy(amount=5)
                await self.seed_qp()
                event = await self.service.create_event(1, 2, "trick_or_treat", grails=1)
                with patch("services.halloween.random.choice", return_value=outcome):
                    result = await self.service.claim(event["id"], 1, 2, 100, action="trick")
                self.assertEqual(result.status, "ok")
                expected_candy = 0 if outcome.qp_loss_percent else {H.HOST_ID: 2, 601700: 4}[outcome.servant_id]
                self.assertEqual(result.candy, expected_candy)
                self.assertEqual(result.grails, 1)
                self.assertFalse(result.complete)
                self.assertEqual(result.servant_id, outcome.servant_id)
                expected_loss = {0: 0, 1: 50, 2: 100}[outcome.qp_loss_percent]
                self.assertEqual(result.qp_lost, expected_loss)
                self.assertEqual(await self.service.balance(1, 100), 5 + expected_candy)
                self.assertEqual(await self.qp_balance(), 5000 - expected_loss)
                self.assertEqual(await self.db.pool.fetchval("SELECT points FROM scores WHERE user_id = 100"), 10000)
                for action in ("treat", "trick"):
                    retry = await self.service.claim(event["id"], 1, 2, 100, action=action)
                    self.assertEqual(retry.status, "duplicate")
                    self.assertEqual(await self.service.balance(1, 100), 5 + expected_candy)
                    self.assertEqual(await self.qp_balance(), 5000 - expected_loss)

    async def test_trick_penalties_scale_round_down_and_stop_at_the_cap(self):
        cases = (
            (None, 0, 0), (0, 0, 0), (1, 0, 0), (49, 0, 0), (50, 0, 1),
            (99, 0, 1), (100, 1, 2), (125, 1, 2), (1000, 10, 20),
            (50000, 500, 1000), (499999, 4999, 9999), (500000, 5000, 10000),
            (500001, 5000, 10000), (1000000, 5000, 10000),
        )
        for starting, liz_loss, mecha_loss in cases:
            for percent, expected in ((1, liz_loss), (2, mecha_loss)):
                with self.subTest(starting=starting, percent=percent):
                    outcome = next(x for x in H.TRICKS if x.qp_loss_percent == percent)
                    if starting is not None:
                        await self.seed_qp(amount=starting)
                    event = await self.service.create_event(1, 2, "trick_or_treat")
                    with patch("services.halloween.random.choice", return_value=outcome):
                        result = await self.service.claim(event["id"], 1, 2, 100, action="trick")
                    self.assertTrue(result.qp_penalty)
                    self.assertEqual(result.qp_lost, expected)
                    self.assertEqual(result.qp_balance, (starting or 0) - expected)
                    self.assertEqual(await self.qp_balance(), (starting or 0) - expected)

    async def test_concurrent_tricks_charge_only_the_claimant_once(self):
        for uid in (100, 200):
            await self.seed_qp(uid=uid)
        event = await self.service.create_event(1, 2, "trick_or_treat")
        outcome = next(x for x in H.TRICKS if x.qp_loss_percent == 1)
        with patch("services.halloween.random.choice", return_value=outcome):
            results = await asyncio.gather(*(
                self.service.claim(event["id"], 1, 2, uid, action="trick") for uid in (100, 100, 200)
            ))
        self.assertEqual(sorted(r.status for r in results), ["duplicate", "ok", "ok"])
        self.assertEqual(sum(r.qp_lost for r in results), 100)
        self.assertEqual(await self.qp_balance(100), 4950)
        self.assertEqual(await self.qp_balance(200), 4950)

    async def test_concurrent_events_calculate_penalties_from_current_balance(self):
        await self.seed_qp(amount=600)
        events = [await self.service.create_event(1, channel, "trick_or_treat") for channel in (2, 3)]
        outcome = next(x for x in H.TRICKS if x.qp_loss_percent == 1)
        with patch("services.halloween.random.choice", return_value=outcome):
            results = await asyncio.gather(*(
                self.service.claim(event["id"], 1, event["channel_id"], 100, action="trick")
                for event in events
            ))
        self.assertTrue(all(result.status == "ok" for result in results))
        self.assertEqual(sorted(result.qp_lost for result in results), [5, 6])
        self.assertEqual(await self.qp_balance(), 589)
        self.assertEqual(await self.db.pool.fetchval("SELECT points FROM scores WHERE user_id = 100"), 10000)

    async def test_treat_and_spill_never_spend_qp(self):
        await self.seed_qp()
        for kind, action in (("trick_or_treat", "treat"), ("candy_spill", "collect")):
            event = await self.service.create_event(1, 2, kind)
            result = await self.service.claim(event["id"], 1, 2, 100, action=action)
            self.assertEqual(result.qp_lost, 0)
            self.assertEqual(await self.qp_balance(), 5000)

    async def test_spill_completion_bonus_is_atomic_and_paid_once_to_everyone(self):
        event = await self.service.create_event(1, 2, "candy_spill", slots=3, grails=1)
        users = [100, 200, 300]
        results = await asyncio.gather(*(
            self.service.claim(event["id"], 1, 2, uid, slot=slot) for slot, uid in enumerate(users)
        ))
        self.assertEqual(sum(r.bonus > 0 for r in results), 1)
        for uid, result in zip(users, results):
            self.assertEqual(result.candy, 1)
            self.assertEqual(await self.service.balance(1, uid), 2)
            self.assertEqual(await self.contracts.grail_balance(1, uid), 1)
        retry = await self.service.claim(event["id"], 1, 2, 300, slot=2)
        self.assertEqual(retry.status, "duplicate")
        self.assertEqual(await self.service.balance(1, 100), 2)

    async def test_expired_wrong_channel_and_invalid_requests_give_nothing(self):
        expired = await self.service.create_event(1, 2, "candy_spill", expires_at=time.time() - 1)
        event = await self.service.create_event(1, 2, "candy_spill")
        requests = [(expired["id"], 1, 2, 0), (event["id"], 9, 2, 0),
                    (event["id"], 1, 9, 0), (event["id"], 1, 2, 100)]
        for eid, gid, cid, slot in requests:
            result = await self.service.claim(eid, gid, cid, 100, slot=slot)
            self.assertNotEqual(result.status, "ok")
        self.assertEqual(await self.service.balance(1, 100), 0)

    async def test_reward_failure_rolls_back_claim_so_player_can_retry(self):
        event = await self.service.create_event(1, 2, "trick_or_treat")
        await self.seed_qp()
        await self.db._conn.executescript(
            "CREATE TRIGGER fail_candy BEFORE INSERT ON grail_balance BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
        )
        outcome = next(x for x in H.TRICKS if x.qp_loss_percent == 2)
        with patch("services.halloween.random.choice", return_value=outcome):
            with self.assertRaisesRegex(Exception, "test failure"):
                await self.service.claim(event["id"], 1, 2, 100, action="trick")
        self.assertEqual(await self.db.pool.fetchval("SELECT COUNT(*) FROM halloween_claims"), 0)
        self.assertEqual(await self.qp_balance(), 5000)
        await self.db.pool.execute("DROP TRIGGER fail_candy")
        self.assertEqual((await self.service.claim(event["id"], 1, 2, 100, action="treat")).status, "ok")

    async def test_feeding_preserves_ownership_and_caps_without_wasting_stock(self):
        for target_id in (100, 200):
            with self.subTest(target_id=target_id):
                await self.seed_candy()
                self.assertEqual((await self.contracts.feed_xp(1, 100, target_id, 502600, "candy", 5))[0], "not_owned")
                self.assertEqual(await self.service.balance(1, 100), 20)
                await self.contracts.contract(1, target_id, 502600)
                await self.contracts.set_active_level(1, target_id, 59)
                result = await self.contracts.feed_xp(1, 100, target_id, 502600, "candy", 20)
                self.assertEqual(result, ("ok", 1, 59, 60, 60))
                self.assertEqual(await self.service.balance(1, 100), 19)
                self.assertEqual((await self.contracts.feed_xp(1, 100, target_id, 502600, "candy"))[0], "at_cap")
                self.assertEqual(await self.service.balance(1, 100), 19)

    async def test_concurrent_feeds_cannot_spend_the_same_candy(self):
        for target_id in (100, 200):
            with self.subTest(target_id=target_id):
                await self.seed_candy(amount=1)
                await self.contracts.contract(1, target_id, 502600)
                results = await asyncio.gather(*(
                    self.contracts.feed_xp(1, 100, target_id, 502600, "candy", 1) for _ in range(2)
                ))
                self.assertEqual(sorted(r[0] for r in results), ["no_items", "ok"])
                self.assertEqual(await self.service.balance(1, 100), 0)
                self.assertEqual((await self.contracts.active(1, target_id))["level"], 31)

    async def test_feed_xp_failure_rolls_back_spending(self):
        await self.db._conn.executescript(
            "CREATE TRIGGER fail_xp BEFORE UPDATE OF level ON servant_contracts "
            "BEGIN SELECT RAISE(ABORT, 'xp failure'); END;"
        )
        for target_id in (100, 200):
            with self.subTest(target_id=target_id):
                await self.seed_candy(amount=3)
                await self.contracts.contract(1, target_id, 502600)
                with self.assertRaisesRegex(Exception, "xp failure"):
                    await self.contracts.feed_xp(1, 100, target_id, 502600, "candy", 3)
                self.assertEqual(await self.service.balance(1, 100), 3)
                self.assertEqual((await self.contracts.active(1, target_id))["level"], 1)

    async def test_one_candy_applies_full_xp_above_base_cap(self):
        await self.seed_candy(amount=1)
        await self.contracts.contract(1, 100, 502600)
        await self.db.pool.execute(
            "UPDATE servant_contracts SET level = 60, grails_used = 4 "
            "WHERE guild_id = 1 AND user_id = 100 AND servant_id = 502600"
        )
        result = await self.contracts.feed_xp(1, 100, 100, 502600, "candy")
        self.assertEqual(result, ("ok", 1, 60, 71, 80))
        self.assertEqual((await self.contracts.active(1, 100))["xp"], 250)
        self.assertEqual(await self.service.balance(1, 100), 0)

    async def test_ordinary_ember_feeding_still_works(self):
        await self.contracts.contract(1, 100, 502600)
        await self.contracts.grant_xp_item(1, 100, "embers", 1)
        result = await self.contracts.feed_xp(1, 100, 100, 502600, "embers")
        self.assertEqual(result, ("ok", 1, 1, 6, 60))
        self.assertEqual(await self.contracts.xp_items(1, 100), (0, 0))

    async def test_unlisted_users_cannot_claim_feed_or_autocomplete(self):
        event = await self.service.create_event(1, 2, "candy_spill")
        view = CandyEventView(self.cog, event, None)
        i = interaction(999)
        await view.claim(i, "collect", 0)
        await self.cog.candy.callback(self.cog, i)
        self.assertEqual(i.response.send_message.await_count, 2)
        self.assertEqual(await self.cog.candy_servants(i, ""), [])
        self.assertEqual(await self.service.balance(1, 999), 0)
        view.stop()

    async def test_feed_embed_has_actual_text_and_face_without_audio_after_end(self):
        self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_END_AT="2020-01-01T00:00:00Z")
        await self.seed_candy()
        await self.contracts.contract(1, 100, 502600)
        i = interaction()
        await self.cog.candy.callback(self.cog, i, quantity=2)
        result = i.followup.send.call_args.kwargs
        self.assertTrue(result["ephemeral"])
        self.assert_liz_portrait(result["embed"], result["files"], "happy")
        self.assertIn("Thanks!", result["embed"].description)
        self.assertIn("3,000 XP per candy", result["embed"].footer.text)
        self.assertNotIn("file", result)
        self.assertEqual(await self.service.balance(1, 100), 18)
        choices = await self.cog.candy_servants(i, "Elisabeth")
        self.assertEqual([c.value for c in choices], [502600])

    async def test_gift_spends_only_givers_candy_and_shows_recipients_reaction_publicly(self):
        await self.seed_candy(amount=5)
        await self.seed_candy(uid=200, amount=9)
        await self.contracts.contract(1, 100, 601700)
        await self.contracts.contract(1, 200, 502600)
        i = interaction()
        recipient = SimpleNamespace(id=200, display_name="Alex")
        await self.cog.candy.callback(self.cog, i, user=recipient)
        self.assertEqual(await self.service.balance(1, 100), 4)
        self.assertEqual(await self.service.balance(1, 200), 9)
        self.assertEqual((await self.contracts.active(1, 100))["level"], 1)
        self.assertEqual((await self.contracts.active(1, 200))["level"], 31)
        i.response.defer.assert_awaited_once_with(ephemeral=True)
        private, public = i.followup.send.await_args_list
        self.assertTrue(private.kwargs["ephemeral"])
        self.assertIn("4 candy left", private.args[0])
        self.assertFalse(public.kwargs["ephemeral"])
        embed = public.kwargs["embed"]
        self.assertIn("**Tester** gave **Alex's Elisabeth Bathory (Halloween)** **1 candy**!", embed.description)
        self.assertIn("Lv 1 -> **31**", embed.description)
        self.assertIn("Thanks!", embed.description)
        self.assert_liz_portrait(embed, public.kwargs["files"], "happy")
        self.assertNotIn("candy left", embed.footer.text)
        self.assertEqual(public.kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_gift_servant_picker_and_explicit_choice_use_recipients_roster(self):
        await self.seed_candy()
        await self.contracts.contract(1, 100, 1000400)
        await self.contracts.contract(1, 200, 601700)
        await self.contracts.contract(1, 200, 502600)
        i = interaction()
        recipient = SimpleNamespace(id=200, display_name="Alex")
        for selected in (recipient, recipient.id):
            i.namespace = SimpleNamespace(user=selected)
            choices = await self.cog.candy_servants(i, "")
            self.assertCountEqual([choice.value for choice in choices], [601700, 502600])
            choices = await self.cog.candy_servants(i, "Carmilla")
            self.assertEqual([choice.value for choice in choices], [601700])
        await self.cog.candy.callback(self.cog, i, user=recipient, servant=601700)
        roster = {row["servant_id"]: row for row in await self.contracts.owned(1, 200)}
        self.assertEqual(roster[601700]["level"], 31)
        self.assertEqual(roster[502600]["level"], 1)
        self.assertEqual((await self.contracts.active(1, 100))["level"], 1)
        embed = i.followup.send.call_args.kwargs["embed"]
        self.assertIn("Alex's Carmilla", embed.description)
        self.assertEqual(embed.thumbnail.url, self.bot.servants.get(601700).face)

    async def test_gift_failures_are_private_and_do_not_spend_candy(self):
        recipient = SimpleNamespace(id=200, display_name="Alex")
        await self.seed_candy(amount=3)
        await self.seed_candy(uid=200, amount=9)

        async def check_failure(expected, *, balance=3, servant=None):
            i = interaction()
            await self.cog.candy.callback(self.cog, i, user=recipient, servant=servant)
            i.followup.send.assert_awaited_once_with(expected, ephemeral=True)
            self.assertEqual(await self.service.balance(1, 100), balance)
            self.assertEqual(await self.service.balance(1, 200), 9)

        await check_failure("Alex has no active servant.")
        await self.contracts.contract(1, 200, 502600)
        await check_failure("Alex doesn't own that servant.", servant=601700)
        await self.seed_candy(amount=0)
        await check_failure("You have no candy.", balance=0)
        self.assertEqual((await self.contracts.active(1, 200))["level"], 1)
        await self.seed_candy(amount=3)
        await self.contracts.set_active_level(1, 200, 60)
        await check_failure("Already at the Lv 60 cap. Use /grail to raise it.")

    async def test_gift_requires_halloween_access_for_both_players(self):
        await self.seed_candy()
        for uid, recipient_id in ((999, 200), (100, 999)):
            with self.subTest(uid=uid, recipient_id=recipient_id):
                await self.contracts.contract(1, recipient_id, 502600)
                i = interaction(uid)
                recipient = SimpleNamespace(id=recipient_id, display_name="Alex")
                i.namespace = SimpleNamespace(user=recipient)
                await self.cog.candy.callback(self.cog, i, user=recipient)
                self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])
                i.response.defer.assert_not_awaited()
                i.followup.send.assert_not_awaited()
                self.assertEqual(await self.cog.candy_servants(i, ""), [])
                self.assertEqual((await self.contracts.active(1, recipient_id))["level"], 1)
        self.assertEqual(await self.service.balance(1, 100), 20)

    async def test_explicit_self_target_still_feeds_privately(self):
        await self.seed_candy(amount=1)
        await self.contracts.contract(1, 100, 502600)
        i = interaction()
        await self.cog.candy.callback(self.cog, i, user=i.user)
        self.assertEqual(i.followup.send.await_count, 1)
        self.assertTrue(i.followup.send.call_args.kwargs["ephemeral"])
        self.assertEqual(await self.service.balance(1, 100), 0)
        self.assertEqual((await self.contracts.active(1, 100))["level"], 31)

    async def test_restricted_thumbnail_and_missing_dialogue_fallback(self):
        self.bot.servants = ServantIndex([Servant(id=10, name="Custom", class_name="caster", rarity=4,
                                                  art={"1": "art"}, figure={}, face="https://example.com/custom.png")])
        self.bot.restrictions.build_allow.return_value = lambda sid, key: False
        await self.seed_candy()
        await self.contracts.contract(1, 100, 10)
        i = interaction()
        await self.cog.candy.callback(self.cog, i)
        embed = i.followup.send.call_args.kwargs["embed"]
        self.assertFalse(embed.thumbnail.url)
        self.assertIn("Custom enjoys", embed.description)

    async def test_routing_replaces_only_eligible_drops(self):
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock())
        self.cog.spawn_event = AsyncMock(return_value=True)
        self.assertTrue(await self.cog.replace_drop(channel, 999, "ember"))
        self.cog.spawn_event.assert_not_awaited()
        self.assertTrue(await self.cog.replace_drop(channel, 100, "grail_single"))
        self.cog.spawn_event.assert_awaited_once_with(channel, "trick_or_treat", slots=1, grails=1)
        self.bot.config = config()
        self.assertFalse(await self.cog.replace_drop(channel, 100, "ember"))
        self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_END_AT="2020-01-01T00:00:00Z")
        self.assertFalse(await self.cog.replace_drop(channel, 100, "ember"))

    async def test_normal_drop_dispatch_is_preserved_with_halloween_off(self):
        contracts = ContractsCog(self.bot)
        self.bot.get_cog = lambda _: None
        contracts._spawn_ember = AsyncMock()
        await contracts._spawn_chat_drop("channel", 100, "ember")
        contracts._spawn_ember.assert_awaited_once_with("channel")

    async def test_active_halloween_never_falls_back_to_any_normal_drop(self):
        contracts = ContractsCog(self.bot)
        self.bot.get_cog = lambda _: self.cog
        self.cog.spawn_event = AsyncMock(return_value=False)
        for kind, method in (("grail_single", "_spawn_single"), ("grail_box", "_spawn_box"),
                             ("ember", "_spawn_ember"), ("ember_box", "_spawn_ember_box")):
            spawner = AsyncMock()
            setattr(contracts, method, spawner)
            for uid in (100, 999):
                with self.subTest(kind=kind, uid=uid):
                    await contracts._spawn_chat_drop("channel", uid, kind)
                    spawner.assert_not_awaited()

    async def test_all_normal_drops_continue_when_halloween_replacement_is_off(self):
        contracts = ContractsCog(self.bot)
        self.bot.get_cog = lambda _: self.cog
        for settings in ({}, {"HALLOWEEN_WHITELIST": "100", "HALLOWEEN_START_AT": "2099-01-01T00:00:00Z"},
                         {"HALLOWEEN_WHITELIST": "100", "HALLOWEEN_RANDOM_EVENTS": "false"},
                         {"HALLOWEEN_WHITELIST": "100", "HALLOWEEN_END_AT": "2020-01-01T00:00:00Z"}):
            self.bot.config = config(**settings)
            for kind, method in (("grail_single", "_spawn_single"), ("grail_box", "_spawn_box"),
                                 ("ember", "_spawn_ember"), ("ember_box", "_spawn_ember_box")):
                for uid in (100, 999):
                    with self.subTest(settings=settings, kind=kind, uid=uid):
                        spawner = AsyncMock()
                        setattr(contracts, method, spawner)
                        await contracts._spawn_chat_drop("channel", uid, kind)
                        spawner.assert_awaited_once_with("channel")

    async def test_triggerevent_allows_normal_events_in_manual_halloween_mode(self):
        self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_RANDOM_EVENTS="false")
        contracts = ContractsCog(self.bot)
        self.bot.get_cog = lambda _: self.cog
        i = interaction()
        i.user.guild_permissions = discord.Permissions(manage_messages=True)
        i.channel = SimpleNamespace(mention="#events")
        for kind, method in (("grail_single", "_spawn_single"), ("grail_box", "_spawn_box"),
                             ("ember", "_spawn_ember"), ("ember_box", "_spawn_ember_box")):
            with self.subTest(kind=kind):
                spawner = AsyncMock()
                setattr(contracts, method, spawner)
                await contracts.triggerevent.callback(
                    contracts, i, app_commands.Choice(name=kind, value=kind),
                )
                spawner.assert_awaited_once_with(i.channel)

    async def test_triggerevent_cannot_bypass_halloween_but_can_still_spawn_qp(self):
        contracts = ContractsCog(self.bot)
        self.bot.get_cog = lambda _: self.cog
        i = interaction()
        i.channel = SimpleNamespace(mention="#events")
        contracts._award_qp = AsyncMock()
        with patch("cogs.contracts.is_mod", return_value=True):
            for kind, method in (("grail_single", "_spawn_single"), ("grail_box", "_spawn_box"),
                                 ("ember", "_spawn_ember"), ("ember_box", "_spawn_ember_box")):
                with self.subTest(kind=kind):
                    spawner = AsyncMock()
                    setattr(contracts, method, spawner)
                    await contracts.triggerevent.callback(
                        contracts, i, app_commands.Choice(name=kind, value=kind),
                    )
                    spawner.assert_not_awaited()
                    self.assertIn("/halloweenevent", i.response.send_message.call_args.args[0])
            await contracts.triggerevent.callback(
                contracts, i, app_commands.Choice(name="QP", value="qp_reward"),
            )
            contracts._award_qp.assert_awaited_once_with(i.channel, i.user)
            self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_END_AT="2020-01-01T00:00:00Z")
            await contracts.triggerevent.callback(
                contracts, i, app_commands.Choice(name="Ember", value="ember"),
            )
            contracts._spawn_ember.assert_awaited_once_with(i.channel)

    async def test_automatic_qp_drops_continue_during_halloween(self):
        contracts = ContractsCog(self.bot)
        contracts._award_qp = AsyncMock()
        contracts._spawn_chat_drop = AsyncMock()
        message = SimpleNamespace(channel=SimpleNamespace(id=2), guild=SimpleNamespace(id=1),
                                  author=SimpleNamespace(id=100))
        with patch("cogs.contracts.random.random", return_value=0), \
                patch("cogs.contracts.time.monotonic", return_value=10000):
            await contracts._maybe_drop_event(message)
        contracts._award_qp.assert_awaited_once_with(message.channel, message.author)
        contracts._spawn_chat_drop.assert_not_awaited()

    async def test_event_opening_stays_the_same_through_claims_and_updates(self):
        for kind, lines, action in (
            ("candy_spill", H.SPILL_LINES, "collect"),
            ("trick_or_treat", H.TRICK_OR_TREAT_LINES, "treat"),
        ):
            with self.subTest(kind=kind):
                event = await self.service.create_event(1, 2, kind)
                with patch("cogs.halloween.random.choice", return_value=lines[0]) as choose:
                    view = CandyEventView(self.cog, event, self.servant.face)
                    self.cog._views.add(view)
                    view.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
                    opening = f'**Liz:** "{lines[0].text}"'
                    self.assertTrue(view.render().description.startswith(opening))
                    await view._update_message()
                    self.assertTrue(view.message.edit.call_args.kwargs["embed"].description.startswith(opening))
                    await view.claim(interaction(), action, 0)
                    if kind == "candy_spill":
                        self.assertTrue(view.render().description.startswith(opening))
                        await view.on_timeout()
                        self.assertTrue(view.render().description.startswith(opening))
                        await view._cleanup_task
                    else:
                        self.assertFalse(view.complete)
                        self.assertFalse(view.is_finished())
                        self.assertTrue(all(not button.disabled for button in view.children))
                        self.assertTrue(view.render().description.startswith(view.outcome_flavor))
                    choose.assert_called_once_with(lines)

    async def test_each_event_opening_sends_and_preserves_its_matching_portrait(self):
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock())
        for kind, lines in (("candy_spill", H.SPILL_LINES), ("trick_or_treat", H.TRICK_OR_TREAT_LINES)):
            for line in lines:
                with self.subTest(kind=kind, line=line.text):
                    with patch("cogs.halloween.random.choice", return_value=line):
                        self.assertTrue(await self.cog.spawn_event(channel, kind, manual=True))
                    sent = channel.send.call_args.kwargs
                    self.assert_liz_portrait(sent["embed"], sent["files"], line.emotion)
                    view = sent["view"]
                    await view._update_message()
                    update = view.message.edit.call_args.kwargs
                    self.assert_liz_portrait(update["embed"], update["attachments"], line.emotion)
                    self.assertIsNot(sent["files"][0], update["attachments"][0])

    async def test_expired_spill_uses_upset_portrait_and_still_deletes_message(self):
        event = await self.service.create_event(1, 2, "candy_spill")
        view = CandyEventView(self.cog, event, self.servant.face)
        self.cog._views.add(view)
        view.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        await view.on_timeout()
        update = view.message.edit.call_args.kwargs
        self.assert_liz_portrait(update["embed"], update["attachments"], "upset")
        await view._cleanup_task
        view.message.delete.assert_awaited_once()

    async def test_local_liz_portraits_respect_art_restrictions(self):
        self.bot.restrictions.build_allow.return_value = lambda sid, key: False
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock())
        self.assertTrue(await self.cog.spawn_event(channel, "trick_or_treat"))
        sent = channel.send.call_args.kwargs
        self.assertFalse(sent["embed"].thumbnail.url)
        self.assertEqual(sent["files"], [])
        i = interaction()
        await sent["view"].claim(i, "treat", 0)
        reply = i.followup.send.call_args.kwargs
        self.assertFalse(reply["embed"].thumbnail.url)
        self.assertEqual(reply["files"], [])
        self.assertEqual(sent["view"].message.edit.call_args.kwargs["attachments"], [])
        self.assertEqual(await self.service.balance(1, 100), 1)

    async def test_missing_liz_assets_fall_back_without_losing_committed_reward(self):
        with TemporaryDirectory() as folder, patch("cogs.halloween._LIZ_FACE_DIR", Path(folder)):
            with self.assertLogs("cogs.halloween", level="WARNING"):
                event = await self.service.create_event(1, 2, "trick_or_treat")
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                self.assertEqual(view.render().thumbnail.url, self.servant.face)
                i = interaction()
                await view.claim(i, "treat", 0)
            reply = i.followup.send.call_args.kwargs
            self.assertEqual(reply["embed"].thumbnail.url, self.servant.face)
            self.assertEqual(reply["files"], [])
            self.assertEqual(await self.service.balance(1, 100), 1)

    async def test_spill_emoji_buttons_keep_their_slots_and_appearance_after_claims(self):
        for slots in (3, 5, 8):
            with self.subTest(slots=slots):
                event = await self.service.create_event(1, 2, "candy_spill", slots=slots)
                with patch("cogs.halloween.random.shuffle", side_effect=lambda emojis: emojis.reverse()):
                    view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                view.message = SimpleNamespace(edit=AsyncMock())
                icons = [str(button.emoji) for button in view.children]
                expected = [H.SPILL_EMOJIS[slot % len(H.SPILL_EMOJIS)] for slot in range(slots)]
                self.assertEqual(icons, expected[::-1])
                self.assertTrue(all(button.label is None for button in view.children))
                self.assertEqual(len({button.custom_id for button in view.children}), slots)
                self.assertEqual([button.row for button in view.children], [slot // 5 for slot in range(slots)])
                await view.children[-1].callback(interaction())
                self.assertEqual(view.claimed_slots, (slots - 1,))
                self.assertTrue(view.children[-1].disabled)
                self.assertTrue(all(not button.disabled for button in view.children[:-1]))
                self.assertEqual([str(button.emoji) for button in view.children], icons)
                self.assertTrue(all(button.label is None for button in view.children))

    async def test_spill_reply_matches_each_clicked_candy(self):
        event = await self.service.create_event(1, 2, "candy_spill", slots=3)
        view = CandyEventView(self.cog, event, self.servant.face)
        self.cog._views.add(view)
        view.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        for slot, button in enumerate(view.children):
            i = interaction(100 * (slot + 1))
            await button.callback(i)
            embed = i.followup.send.call_args.kwargs["embed"]
            self.assertEqual(embed.title, f"{button.emoji} Candy Collected!")
            self.assertIn("You took a candy from Liz's stash!", embed.description)

    async def test_successful_spill_callbacks_disable_buttons_and_report_bonus(self):
        event = await self.service.create_event(1, 2, "candy_spill", slots=2)
        view = CandyEventView(self.cog, event, self.servant.face)
        view.message = SimpleNamespace(edit=AsyncMock())
        self.cog._views.add(view)
        self.cog._channels[2] = {view}
        first, last = interaction(100), interaction(200)
        await asyncio.gather(view.claim(first, "collect", 0), view.claim(last, "collect", 1))
        self.assertTrue(view.complete)
        self.assertTrue(all(b.disabled for b in view.children))
        self.assertFalse(self.cog._channels)
        self.assertIn("1 bonus candy", last.followup.send.call_args.kwargs["embed"].description)
        self.assertIn("1 bonus candy", view.render().description)
        for i in (first, last):
            reply = i.followup.send.call_args.kwargs
            self.assert_liz_portrait(reply["embed"], reply["files"], "embarrassed")
        update = view.message.edit.call_args.kwargs
        self.assert_liz_portrait(update["embed"], update["attachments"], "happy")
        before = await self.service.balance(1, 100)
        await view.claim(first, "collect", 0)
        self.assertEqual(await self.service.balance(1, 100), before)

    async def test_trick_updates_both_portraits_to_the_speaker(self):
        for outcome in H.TRICKS:
            with self.subTest(outcome=outcome):
                await self.seed_qp()
                event = await self.service.create_event(1, 2, "trick_or_treat")
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                view.message = SimpleNamespace(edit=AsyncMock())
                initial = view.render()
                self.assertTrue(initial.thumbnail.url.startswith("attachment://halloween-liz-"))
                i = interaction()
                with patch("services.halloween.random.choice", return_value=outcome):
                    await view.claim(i, "trick", 0)
                reply = i.followup.send.call_args.kwargs
                face = self.bot.servants.get(outcome.servant_id).face
                self.assertTrue(reply["ephemeral"])
                update = view.message.edit.call_args.kwargs
                if outcome.servant_id == H.HOST_ID:
                    self.assert_liz_portrait(reply["embed"], reply["files"], outcome.emotion)
                    self.assert_liz_portrait(update["embed"], update["attachments"], outcome.emotion)
                    self.assertIsNot(reply["files"][0], update["attachments"][0])
                else:
                    self.assertEqual(reply["embed"].thumbnail.url, face)
                    self.assertEqual(update["embed"].thumbnail.url, face)
                    self.assertEqual(reply["files"], [])
                    self.assertEqual(update["attachments"], [])
                self.assertIn(outcome.flavor, view.render().description)
                if outcome.qp_loss_percent:
                    expected_loss = {1: 50, 2: 100}[outcome.qp_loss_percent]
                    self.assertIn(f"Lost **{expected_loss:,} QP**", reply["embed"].description)

    async def test_restricted_or_missing_guest_portrait_does_not_retain_liz(self):
        outcome = next(x for x in H.TRICKS if x.servant_id == 601700)
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.bot.servants = ServantIndex([self.servant])
                self.bot.restrictions.build_allow.return_value = lambda sid, key: sid != 601700
                event = await self.service.create_event(1, 2, "trick_or_treat")
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                i = interaction()
                with patch("services.halloween.random.choice", return_value=outcome):
                    await view.claim(i, "trick", 0)
                self.assertFalse(view.render().thumbnail.url)
                self.assertFalse(i.followup.send.call_args.kwargs["embed"].thumbnail.url)

    async def test_portrait_lookup_failure_still_reports_committed_trick_result(self):
        for outcome in H.TRICKS:
            with self.subTest(outcome=outcome):
                await self.seed_qp()
                event = await self.service.create_event(1, 2, "trick_or_treat")
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                view.message = SimpleNamespace(edit=AsyncMock())
                self.bot.restrictions.build_allow.side_effect = RuntimeError("restriction lookup failed")
                i = interaction()
                with patch("services.halloween.random.choice", return_value=outcome):
                    with self.assertLogs("cogs.halloween", level="WARNING"):
                        await view.claim(i, "trick", 0)
                reply = i.followup.send.call_args.kwargs["embed"]
                self.assertIn(outcome.flavor, reply.description)
                self.assertFalse(reply.thumbnail.url)
                self.assertFalse(view.render().thumbnail.url)
                self.assertFalse(view.complete)
                expected_loss = {0: 0, 1: 50, 2: 100}[outcome.qp_loss_percent]
                self.assertEqual(await self.qp_balance(), 5000 - expected_loss)
                await view.claim(i, "trick", 0)
                self.assertEqual(await self.qp_balance(), 5000 - expected_loss)

    async def test_trick_response_reports_only_qp_actually_taken(self):
        outcome = next(x for x in H.TRICKS if x.qp_loss_percent == 2)
        for starting, expected_loss in ((0, 0), (25, 0), (125, 2), (1000000, 10000)):
            with self.subTest(starting=starting):
                await self.seed_qp(amount=starting)
                event = await self.service.create_event(1, 2, "trick_or_treat")
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                i = interaction()
                with patch("services.halloween.random.choice", return_value=outcome):
                    await view.claim(i, "trick", 0)
                embed = i.followup.send.call_args.kwargs["embed"]
                text = embed.description
                self.assertIn(f"{starting - expected_loss:,} QP left", embed.footer.text)
                if expected_loss:
                    self.assertIn(f"Lost **{expected_loss:,} QP**", text)
                else:
                    self.assertIn("No QP lost.", text)
                    self.assertNotIn("Lost **", text)

    async def test_channel_gate_single_active_event_and_timeout(self):
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock())
        self.bot.guild_config.is_event_channel_allowed.return_value = False
        self.assertFalse(await self.cog.spawn_event(channel, "candy_spill"))
        channel.send.assert_not_awaited()
        self.bot.guild_config.is_event_channel_allowed.return_value = True
        results = await asyncio.gather(*(self.cog.spawn_event(channel, "candy_spill") for _ in range(2)))
        self.assertEqual(sorted(results), [False, True])
        view = next(iter(self.cog._views))
        await view.on_timeout()
        self.assertTrue(all(b.disabled for b in view.children))
        self.assertFalse(self.cog._channels)

    async def test_manual_events_work_outside_automatic_channels(self):
        self.bot.guild_config.is_event_channel_allowed.return_value = False
        channel_id = 10
        for kind, name in (("trick_or_treat", "Trick or Treat"), ("candy_spill", "Candy Spill")):
            for explicit_channel in (False, True):
                with self.subTest(kind=kind, explicit_channel=explicit_channel):
                    channel_id += 1
                    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
                    target = SimpleNamespace(id=channel_id, guild=SimpleNamespace(id=1),
                                             mention=f"<#{channel_id}>", send=AsyncMock(return_value=message))
                    i = interaction()
                    i.user.guild_permissions = discord.Permissions(manage_messages=True)
                    i.channel = SimpleNamespace(send=AsyncMock()) if explicit_channel else target
                    await HalloweenCog.halloweenevent.callback(
                        self.cog, i, app_commands.Choice(name=name, value=kind),
                        channel=target if explicit_channel else None,
                    )
                    target.send.assert_awaited_once()
                    self.assertIn(target.id, self.cog._channels)
                    i.followup.send.assert_awaited_once_with(f"Started {name} in {target.mention}.", ephemeral=True)
                    if explicit_channel:
                        i.channel.send.assert_not_awaited()
        self.bot.guild_config.is_event_channel_allowed.assert_not_awaited()
        automatic_channel = SimpleNamespace(id=20, guild=SimpleNamespace(id=1), send=AsyncMock())
        self.assertTrue(await self.cog.replace_drop(automatic_channel, 100, "ember"))
        self.bot.guild_config.is_event_channel_allowed.assert_awaited_once_with(1, 20, None)
        automatic_channel.send.assert_not_awaited()

    async def test_manual_mode_can_spawn_claim_and_feed_without_random_events(self):
        self.bot.config = config(HALLOWEEN_WHITELIST="100,200", HALLOWEEN_RANDOM_EVENTS="false")
        self.bot.guild_config.is_event_channel_allowed.return_value = False
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), mention="#testing", send=AsyncMock())
        for kind, action in (("trick_or_treat", "treat"), ("candy_spill", "collect")):
            with self.subTest(kind=kind):
                self.assertFalse(await self.cog.spawn_event(channel, kind))
                before = channel.send.await_count
                i = interaction()
                i.user.guild_permissions = discord.Permissions(manage_messages=True)
                i.channel = channel
                await self.cog.halloweenevent.callback(self.cog, i, app_commands.Choice(name=kind, value=kind))
                self.assertEqual(channel.send.await_count, before + 1)
                view = channel.send.call_args.kwargs["view"]
                await view.claim(interaction(200), action, 0)
        self.bot.guild_config.is_event_channel_allowed.assert_not_awaited()
        self.assertEqual(await self.service.balance(1, 200), 2)
        await self.contracts.contract(1, 200, H.HOST_ID)
        await self.cog.candy.callback(self.cog, interaction(200), quantity=2)
        self.assertEqual(await self.service.balance(1, 200), 0)
        self.assertGreater((await self.contracts.active(1, 200))["level"], 1)

    async def test_manual_event_requires_whitelist_and_staff_access(self):
        self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_RANDOM_EVENTS="false")
        self.cog.spawn_event = AsyncMock(return_value=True)
        cases = (
            (999, True, False, "You don't have access to the Halloween event."),
            (999, False, True, "You don't have access to the Halloween event."),
            (100, False, False, "Only moderators can start events."),
            (100, False, True, None),
        )
        for uid, moderator, owner, denial in cases:
            with self.subTest(uid=uid, moderator=moderator, owner=owner):
                self.cog.spawn_event.reset_mock()
                self.bot.is_owner = AsyncMock(return_value=owner)
                i = interaction(uid)
                i.user.guild_permissions = discord.Permissions(manage_messages=moderator)
                i.channel = SimpleNamespace(mention="#testing")
                await HalloweenCog.halloweenevent.callback(
                    self.cog, i, app_commands.Choice(name="Trick or Treat", value="trick_or_treat"),
                )
                if denial:
                    i.response.send_message.assert_awaited_once_with(denial, ephemeral=True)
                    self.cog.spawn_event.assert_not_awaited()
                else:
                    self.cog.spawn_event.assert_awaited_once_with(i.channel, "trick_or_treat", manual=True)

    async def test_manual_events_still_respect_active_dates(self):
        now = dt.datetime.now(dt.timezone.utc)
        for values in (
            {"HALLOWEEN_START_AT": (now + dt.timedelta(days=1)).isoformat()},
            {"HALLOWEEN_END_AT": (now - dt.timedelta(days=1)).isoformat()},
        ):
            with self.subTest(values=values):
                self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_RANDOM_EVENTS="false", **values)
                i = interaction()
                i.user.guild_permissions = discord.Permissions(manage_messages=True)
                i.channel = SimpleNamespace(id=2, send=AsyncMock())
                await HalloweenCog.halloweenevent.callback(
                    self.cog, i, app_commands.Choice(name="Candy Spill", value="candy_spill"),
                )
                i.channel.send.assert_not_awaited()
                self.assertFalse(self.cog._channels)
                i.followup.send.assert_awaited_once_with("Halloween events aren't active right now.", ephemeral=True)

    async def test_manual_events_can_overlap_and_be_claimed_independently(self):
        self.bot.guild_config.is_event_channel_allowed.return_value = False
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), mention="#testing", send=AsyncMock())
        choices = [app_commands.Choice(name="Candy Spill", value="candy_spill"),
                   app_commands.Choice(name="Candy Spill", value="candy_spill"),
                   app_commands.Choice(name="Trick or Treat", value="trick_or_treat")]
        interactions = [interaction() for _ in choices]
        for i in interactions:
            i.channel = channel
            i.user.guild_permissions = discord.Permissions(manage_messages=True)
        await asyncio.gather(*(
            HalloweenCog.halloweenevent.callback(self.cog, i, choice)
            for i, choice in zip(interactions, choices)
        ))
        self.assertEqual(channel.send.await_count, 3)
        for i, choice in zip(interactions, choices):
            i.followup.send.assert_awaited_once_with(f"Started {choice.name} in #testing.", ephemeral=True)
        views = tuple(self.cog._views)
        self.assertEqual(len({view.event["id"] for view in views}), 3)
        for _ in range(2):  # One claim in each event, followed by rejected duplicate clicks.
            await asyncio.gather(*(
                view.claim(interaction(), "collect" if view.event["kind"] == "candy_spill" else "treat", 0)
                for view in views
            ))
            self.assertEqual(await self.service.balance(1, 100), 3)
        self.assertEqual(self.cog._channels[2], set(views))
        self.bot.guild_config.is_event_channel_allowed.return_value = True
        self.assertFalse(await self.cog.spawn_event(channel, "candy_spill"))

    async def test_expiring_one_event_does_not_end_others_or_unblock_automatic_drops(self):
        messages = [SimpleNamespace(edit=AsyncMock(), delete=AsyncMock()) for _ in range(3)]
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock(side_effect=messages))
        self.assertTrue(await self.cog.spawn_event(channel, "candy_spill"))
        first = next(iter(self.cog._views))
        self.assertTrue(await self.cog.spawn_event(channel, "candy_spill", manual=True))
        second = next(view for view in self.cog._views if view is not first)
        await first.on_timeout()
        await first._cleanup_task
        messages[0].delete.assert_awaited_once()
        messages[1].delete.assert_not_awaited()
        await first.on_timeout()  # Releasing an ended event again must be harmless.
        self.assertEqual(self.cog._channels[2], {second})
        self.assertTrue(all(not button.disabled for button in second.children))
        self.assertFalse(await self.cog.spawn_event(channel, "candy_spill"))
        await second.on_timeout()
        await second._cleanup_task
        messages[1].delete.assert_awaited_once()
        self.assertFalse(self.cog._channels)
        self.assertTrue(await self.cog.spawn_event(channel, "candy_spill"))

    async def test_failed_manual_spawn_does_not_release_another_events_channel(self):
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock(
            side_effect=lambda **kwargs: SimpleNamespace(edit=AsyncMock(), delete=AsyncMock()),
        ))
        self.assertTrue(await self.cog.spawn_event(channel, "candy_spill", manual=True))
        first = next(iter(self.cog._views))
        for operation in ("create_event", "set_deadline"):
            with self.subTest(operation=operation):
                with patch.object(self.cog.service, operation, side_effect=RuntimeError("database unavailable")):
                    with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                        await self.cog.spawn_event(channel, "candy_spill", manual=True)
                self.assertEqual(self.cog._views, {first})
                self.assertEqual(self.cog._channels[2], {first})
                self.assertTrue(all(not button.disabled for button in first.children))
                first.message.delete.assert_not_awaited()
                self.assertFalse(await self.cog.spawn_event(channel, "candy_spill"))

    async def test_discord_message_updates_leave_unclaimed_buttons_enabled(self):
        for kind in ("trick_or_treat", "candy_spill"):
            with self.subTest(kind=kind):
                event = await self.service.create_event(1, 2, kind)
                view = CandyEventView(self.cog, event, self.servant.face)
                self.cog._views.add(view)
                store = ViewStore(SimpleNamespace())
                store.add_view(view, message_id=123)
                store.update_from_message(123, view.to_components())
                self.assertTrue(view.is_dispatching())
                self.assertFalse(view.is_finished())
                self.assertFalse(view.expired)
                self.assertTrue(all(not button.disabled for button in view.children))
                self.assertEqual(await self.db.pool.fetchval(
                    "SELECT COUNT(*) FROM halloween_claims WHERE event_id = $1", event["id"],
                ), 0)
                view.stop()

    async def test_trick_or_treat_buttons_stay_open_for_independent_choices_until_timeout(self):
        self.bot.config = config(HALLOWEEN_WHITELIST="all", HALLOWEEN_RANDOM_EVENTS="false")
        message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock(return_value=message))
        self.assertTrue(await self.cog.spawn_event(channel, "trick_or_treat", manual=True))
        view = next(iter(self.cog._views))
        deadline = view.event["expires_at"]
        expiry_task = view._expiry_task
        store = ViewStore(SimpleNamespace())
        store.add_view(view, message_id=123)
        self.assertEqual(view.render().footer.text, "0 played | 1 per player")
        for uid, outcome in enumerate(H.TRICKS, start=100):
            await self.seed_qp(uid=uid)
            i = interaction(uid)
            with patch("services.halloween.random.choice", return_value=outcome):
                await view.children[1].callback(i)
            self.assertEqual(await self.service.balance(1, uid), outcome.candy)
            self.assertIn(outcome.flavor, i.followup.send.call_args.kwargs["embed"].description)
            store.update_from_message(123, view.to_components())
            self.assertTrue(all(not button.disabled for button in view.children))
        # Treat remains available after other players have rolled every Trick outcome.
        for uid in range(200, 210):
            await view.children[0].callback(interaction(uid))
            self.assertEqual(await self.service.balance(1, uid), 1)
        retry = interaction(100)
        await view.children[0].callback(retry)
        retry.followup.send.assert_awaited_once_with("You already chose Trick or Treat!", ephemeral=True)
        self.assertEqual(await self.service.balance(1, 100), H.TRICKS[0].candy)
        self.assertEqual(len(view.claimed_slots), 14)
        self.assertEqual(view.render().footer.text, "14 played | 1 per player")
        self.assertEqual(len(view.claim_log), 5)
        self.assertEqual(view.render().fields[0].name, "Recent results")
        self.assertFalse(view.complete)
        self.assertFalse(view.is_finished())
        self.assertTrue(all(not button.disabled for button in view.children))
        self.assertEqual(view.event["expires_at"], deadline)
        self.assertIs(view._expiry_task, expiry_task)
        self.assertIsNone(view._cleanup_task)
        message.delete.assert_not_awaited()
        self.assertEqual(self.cog._channels[2], {view})

        await view.on_timeout()
        await asyncio.wait_for(view._cleanup_task, timeout=1)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(button.disabled for button in view.children))
        self.assertFalse(self.cog._channels)
        message.delete.assert_awaited_once()
        late = interaction(999)
        await view.children[0].callback(late)
        late.followup.send.assert_awaited_once_with("Event ended.", ephemeral=True)
        self.assertEqual(await self.service.balance(1, 999), 0)

    async def test_slow_message_post_does_not_use_up_the_event_window(self):
        message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())

        async def send(**kwargs):
            await asyncio.sleep(0.25)
            return message

        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=send)
        with patch.object(H, "EVENT_SECONDS", 0.2):
            self.assertTrue(await self.cog.spawn_event(channel, "trick_or_treat"))
        self.assertEqual(len(self.cog._views), 1)
        view = next(iter(self.cog._views))
        self.assertFalse(view.expired)
        self.assertTrue(all(not button.disabled for button in view.children))
        stored_expiry = await self.db.pool.fetchval(
            "SELECT expires_at FROM halloween_events WHERE id = $1", view.event["id"],
        )
        self.assertGreater(stored_expiry, time.time())
        result = await self.service.claim(view.event["id"], 1, 2, 100, action="treat")
        self.assertEqual(result.status, "ok")

    async def test_expired_message_is_pruned_even_if_the_final_edit_fails(self):
        event = await self.service.create_event(1, 2, "candy_spill")
        view = CandyEventView(self.cog, event, None)
        self.cog._views.add(view)
        view.message = SimpleNamespace(
            edit=AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="failure"), "test")),
            delete=AsyncMock(),
        )
        with self.assertLogs("cogs.halloween", level="WARNING"):
            await view.on_timeout()
        await asyncio.wait_for(view._cleanup_task, timeout=1)
        view.message.delete.assert_awaited_once_with()
        self.assertTrue(all(button.disabled for button in view.children))
        self.assertFalse(self.cog._channels)

    async def test_posted_event_is_deleted_if_deadline_activation_fails(self):
        message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock(return_value=message))
        self.cog.service.set_deadline = AsyncMock(side_effect=RuntimeError("database unavailable"))
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await self.cog.spawn_event(channel, "candy_spill")
        message.delete.assert_awaited_once_with()
        self.assertFalse(self.cog._channels)
        self.assertFalse(self.cog._views)

    async def test_posting_event_does_not_extend_the_seasonal_end(self):
        end = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)).isoformat()
        self.bot.config = config(HALLOWEEN_WHITELIST="100", HALLOWEEN_END_AT=end)
        message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        channel = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), send=AsyncMock(return_value=message))
        self.assertTrue(await self.cog.spawn_event(channel, "candy_spill"))
        view = next(iter(self.cog._views))
        self.assertEqual(view.event["expires_at"], self.bot.config.halloween_end_at.timestamp())

    async def test_event_expires_at_absolute_deadline_without_a_click(self):
        for kind in ("candy_spill", "trick_or_treat"):
            with self.subTest(kind=kind):
                event = await self.service.create_event(1, 2, kind, expires_at=time.time() + 0.02)
                view = CandyEventView(self.cog, event, None)
                self.cog._views.add(view)
                self.cog._channels[2] = {view}
                view.start_timer()
                await asyncio.wait_for(view._expiry_task, timeout=1)
                self.assertTrue(view.expired)
                self.assertTrue(view.is_finished())
                self.assertFalse(self.cog._channels)


if __name__ == "__main__":
    unittest.main()
