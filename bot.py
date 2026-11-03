"""执行层：一个会运营、会出兵、会听指令的神族 bot。微操交给游戏本身，大脑交给 LLM。"""

from __future__ import annotations

import traceback

import sc2.unit as _sc2_unit
from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId as U

ARMY_TYPES = (U.ZEALOT, U.STALKER)


def _safe_orders(self):
    # 5.0.17 会下发 python-sc2 自带数据里没有的 ability id，原版 orders 会直接 KeyError，
    # 而 train() 内部依赖 len(structure.orders)。跳过未知指令即可，不用中断整局。
    orders = []
    for order in self._proto.orders:
        try:
            orders.append(_sc2_unit.UnitOrder.from_proto(order, self._bot_object))
        except KeyError:
            continue
    return orders


_sc2_unit.Unit.orders = property(_safe_orders)


class ExecutorBot(BotAI):
    def __init__(self, brain) -> None:
        super().__init__()
        self.brain = brain
        self._gates: dict[str, float] = {}
        self._last_state_push = 0.0
        self._reported_errors = 0
        self._scout_tag: int | None = None

    async def on_start(self) -> None:
        print(f"[bot] 开局：{self.race.name} vs {self.enemy_race.name if self.enemy_race else '?'}")

    async def on_step(self, iteration: int) -> None:
        try:
            await self._maybe_chat()
            self._push_state()
            if self._gate("house", 22):
                await self._housekeeping()
            if self._gate("build", 18):
                await self._build_order()
            self._train_army()
            if self._gate("micro", 22):
                self._micro()
            if self._gate("scout", 66):
                self._scout()
        except Exception:
            self._reported_errors += 1
            if self._reported_errors <= 3:
                traceback.print_exc()

    def _gate(self, key: str, min_steps: int) -> bool:
        loop = self.state.game_loop
        if loop - self._gates.get(key, -10_000) >= min_steps:
            self._gates[key] = loop
            return True
        return False

    async def _maybe_chat(self) -> None:
        message = self.brain.take_message()
        if message:
            await self.chat_send(message)

    def _push_state(self) -> None:
        loop = self.state.game_loop
        if loop - self._last_state_push < 22:
            return
        self._last_state_push = loop
        if self._gate("status", 110):
            print(
                f"[bot] {self.time:5.0f}s 人口{int(self.supply_used)}/{int(self.supply_cap)} "
                f"农民{self.workers.amount} 兵{int(self.supply_army)} "
                f"基地{self.townhalls.amount} 矿{int(self.minerals)} 气{int(self.vespene)} "
                f"敌兵{self.enemy_units.amount}"
            )
        self.brain.update_state(
            {
                "time": round(self.time),
                "my_race": self.race.name,
                "supply": f"{int(self.supply_used)}/{int(self.supply_cap)}",
                "army_supply": int(self.supply_army),
                "workers": self.workers.amount,
                "bases": self.townhalls.amount,
                "minerals": int(self.minerals),
                "gas": int(self.vespene),
                "army": {u.name.lower(): self.units(u).amount for u in ARMY_TYPES},
                "gateways": self.structures(U.GATEWAY).amount,
                "enemy_visible_units": self.enemy_units.amount,
                "enemy_bases_seen": self.enemy_structures.of_type(
                    {U.NEXUS, U.COMMANDCENTER, U.ORBITALCOMMAND, U.PLANETARYFORTRESS, U.HATCHERY, U.LAIR, U.HIVE}
                ).amount,
            }
        )

    async def _housekeeping(self) -> None:
        await self.distribute_workers()
        if not self.townhalls.ready:
            return
        if self.workers.amount < 20 * self.townhalls.amount and self.supply_left > 0:
            self.train(U.PROBE, 2)

    async def _build_order(self) -> None:
        if not self.townhalls:
            return
        if not self.townhalls.ready:
            return
        main = self.townhalls.first
        if self.supply_left <= 3 and self.already_pending(U.PYLON) == 0 and self.can_afford(U.PYLON):
            await self.build(U.PYLON, near=main)
            return

        gateways = self.structures(U.GATEWAY).amount + self.already_pending(U.GATEWAY)
        if gateways == 0 and self.can_afford(U.GATEWAY):
            await self.build(U.GATEWAY, near=main)
            return

        cyber = self.structures(U.CYBERNETICSCORE).amount + self.already_pending(U.CYBERNETICSCORE)
        if cyber == 0 and self.structures(U.GATEWAY).ready and self.can_afford(U.CYBERNETICSCORE):
            await self.build(U.CYBERNETICSCORE, near=main)
            return

        if (
            cyber > 0
            and self.structures(U.GATEWAY).amount < 2
            and self.minerals >= 150
            and self.can_afford(U.GATEWAY)
        ):
            await self.build(U.GATEWAY, near=main)
            return

        if self.gas_buildings.amount < 2 and self.workers.amount >= 14 and self.can_afford(U.ASSIMILATOR):
            free_geysers = [
                g
                for g in self.vespene_geyser
                if all(g.distance_to(b) > 3 for b in self.gas_buildings)
            ]
            if free_geysers:
                await self.build(U.ASSIMILATOR, near=free_geysers[0])
                return

        directive = self.brain.directive
        if (
            self.townhalls.amount == 1
            and self.time > 120
            and directive["expand"]
            and self.can_afford(U.NEXUS)
        ):
            await self.expand_now()
            return

        if (
            self.structures(U.CYBERNETICSCORE).ready
            and self.structures(U.GATEWAY).amount < 4
            and self.minerals > 200
            and self.can_afford(U.GATEWAY)
        ):
            await self.build(U.GATEWAY, near=main)

    def _train_army(self) -> None:
        if self.supply_left <= 0:
            return
        if not self.structures(U.GATEWAY).ready.idle:
            return
        composition = self.brain.directive["composition"]
        zealots = self.units(U.ZEALOT).amount
        stalkers = self.units(U.STALKER).amount
        if composition == "zealot":
            self.train(U.ZEALOT)
        elif composition == "stalker" and self.structures(U.CYBERNETICSCORE).ready:
            self.train(U.STALKER)
        elif zealots <= stalkers:
            self.train(U.ZEALOT)
        elif self.structures(U.CYBERNETICSCORE).ready:
            self.train(U.STALKER)

    def _micro(self) -> None:
        army = self.units.of_type(ARMY_TYPES)
        if not army:
            return

        threats = self.enemy_units.filter(
            lambda u: u.distance_to(self.start_location) < 30
            or any(u.distance_to(t) < 25 for t in self.townhalls)
        )
        if threats:
            for unit in army:
                unit.attack(threats.closest_to(unit))
            return

        directive = self.brain.directive
        stance = directive["stance"]
        if stance == "defend":
            return

        if stance == "all_in" or self.supply_army >= directive["attack_at_army"]:
            target = self._attack_target()
            for unit in army.idle:
                unit.attack(target)
            return

        if stance == "harass" and len(army) >= 5 and self.time > 240:
            raiders = army.idle.sorted(lambda u: u.distance_to(self.enemy_start_locations[0]))[:3]
            for unit in raiders:
                unit.attack(self.enemy_start_locations[0])

    def _attack_target(self):
        if self.enemy_start_locations:
            return self.enemy_start_locations[0]
        return self.game_info.map_center

    def _scout(self) -> None:
        if not self.enemy_start_locations:
            return
        if self._scout_tag is not None:
            scout = self.workers.find_by_tag(self._scout_tag)
            if scout is None:
                self._scout_tag = None
            else:
                scout.move(self.enemy_start_locations[0])
            return
        if self.time < 45:
            return
        idle = self.workers.idle
        if idle:
            scout = idle.first
            self._scout_tag = scout.tag
            scout.move(self.enemy_start_locations[0])
