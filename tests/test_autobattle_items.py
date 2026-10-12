from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from cogs.autobattle import Autobattle, ShopView
from data import autobattle_engine as engine
from data.autobattle_effects import process_effect_durations
from data.servants import Servant, ServantIndex
from db import Database
from services.contracts import ContractService
from services.scoring import ScoringService

ROOT = Path(__file__).resolve().parents[1]


class AutobattleItemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database("sqlite::memory:")
        await self.db.connect()
        for migration in sorted((ROOT / "database/migrations").glob("*.sql")):
            await self.db._conn.executescript(migration.read_text().split("-- migrate:down")[0])
        self.contracts = ContractService(self.db.pool)
        self.scoring = ScoringService(self.db.pool)
        self.liz = Servant(id=502600, name="Elisabeth Bathory (Halloween)", class_name="caster",
                           rarity=4, art={}, figure={})
        carmilla = Servant(id=601700, name="Carmilla", class_name="assassin", rarity=4, art={}, figure={})
        self.cog = Autobattle(SimpleNamespace(
            contracts=self.contracts, scoring=self.scoring, kits=None,
            servants=ServantIndex([self.liz, carmilla]),
        ))

    async def asyncTearDown(self):
        await self.db.close()

    async def test_shop_charges_five_million_for_lens_and_marks_it_reusable(self):
        await self.scoring.award(1, 100, 6000000)
        view = ShopView(self.cog, 1, 100)
        try:
            index = next(i for i, (key, _) in enumerate(view.items) if key == "divine_lens")
            view.page = index // view.ITEMS_PER_PAGE
            view._build_buttons()
            button = next(b for b in view.children if b.label and b.label.startswith("Divine Lens"))
            self.assertIn("5,000,000 QP", button.label)
            interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
            await button.callback(interaction)
            self.assertEqual(await self.scoring.get_balance(1, 100), 1000000)
            self.assertEqual(await self.contracts.item_qty(1, 100, "divine_lens"), 1)
            embed = interaction.response.edit_message.call_args.kwargs["embed"]
            field = next(field for field in embed.fields if "Divine Lens" in field.name)
            self.assertIn("Reusable", field.value)
        finally:
            view.stop()

    async def test_lens_survives_repeated_fights_while_consumables_are_spent(self):
        await self.contracts.contract(1, 100, self.liz.id)
        await self.contracts.contract(1, 100, 601700)
        await self.contracts.add_item(1, 100, "divine_lens", 1)
        await self.contracts.add_item(1, 100, "ampoule", 2)
        await self.contracts.equip(1, 100, self.liz.id, "divine_lens")
        await self.contracts.equip(1, 100, 601700, "ampoule")
        for fight in range(2):
            team = await self.cog._side(1, 100, [self.liz.id, 601700])
            initial = {c["servant_id"]: c["equipped_item"] for c in team}
            base_hp = team[0]["max_hp"]
            for combatant in team:
                engine.apply_item_effects(combatant, "on_enter", [])
            self.assertEqual(team[0]["max_hp"], 6 * base_hp)
            lines = await self.cog._consume_items(1, 100, team, initial)
            self.assertTrue(any("Divine Lens (reusable)" in line for line in lines))
            self.assertEqual(await self.contracts.item_qty(1, 100, "divine_lens"), 1)
            self.assertEqual(await self.contracts.item_qty(1, 100, "ampoule"), 1 - fight)
        team = await self.cog._side(1, 100, [self.liz.id, 601700])
        self.assertEqual(team[0]["equipped_item"], "divine_lens")
        self.assertIsNone(team[1]["equipped_item"])
        self.assertEqual((await self.contracts.equips(1, 100))[self.liz.id], "divine_lens")

    async def test_reusable_lens_activates_once_per_fight_and_lasts_five_turns(self):
        combatant = engine.combatant(self.liz, 1, 0, item="divine_lens")
        base_hp = combatant["max_hp"]
        engine.apply_item_effects(combatant, "on_enter", [])
        self.assertEqual(combatant["max_hp"], 6 * base_hp)
        self.assertEqual(combatant["current_hp"], 6 * base_hp)
        for _ in range(4):
            process_effect_durations(combatant)
        # Returning to the front must neither stack nor refresh the HP boost.
        engine.apply_item_effects(combatant, "on_enter", [])
        self.assertEqual(combatant["max_hp"], 6 * base_hp)
        process_effect_durations(combatant)
        self.assertEqual(combatant["max_hp"], base_hp)
        self.assertEqual(combatant["current_hp"], base_hp)
        engine.apply_item_effects(combatant, "on_enter", [])
        self.assertEqual(combatant["max_hp"], base_hp)


if __name__ == "__main__":
    unittest.main()
