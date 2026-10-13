from f1 import hardened, idealist_support, play


class _Stub:
    def __init__(self, carried: set[int]) -> None:
        self.carried, self.worn = carried, []

    def _count(self, pid: int) -> int:
        return int(pid in self.carried)

    def equip(self, pid: int, slot: str) -> None:
        self.worn.append((pid, slot))

    def ready_weapon(self) -> None:
        pass

    def holster(self, on: bool) -> None:
        pass


def test_the_hardened_route_runs_before_the_cathedral() -> None:
    chain = play.CHAINS["idealist"]
    assert chain.index("idealist_watershed") < chain.index("idealist_hardened") < chain.index("idealist_cathedral")
    assert [s.name for s in hardened.IDEALIST_HARDENED][-1] == "Miles: the Hardened Power Armor"


def test_rearm_wears_the_hardened_armour_first() -> None:
    both = _Stub({idealist_support.PID_POWER_ARMOR, idealist_support.PID_HARDENED_POWER_ARMOR})
    idealist_support.rearm(both)
    assert both.worn == [(idealist_support.PID_HARDENED_POWER_ARMOR, "armor")]
    plain = _Stub({idealist_support.PID_POWER_ARMOR})
    idealist_support.rearm(plain)
    assert plain.worn == [(idealist_support.PID_POWER_ARMOR, "armor")]
