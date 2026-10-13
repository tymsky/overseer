from f1 import trade
from f1.actions import PID_STIMPAK

CELLS, JHP, RIFLE, ARMOR = 39, 30, 10, 1


def test_buys_follow_the_wants_in_order_within_the_budget() -> None:
    stock = {CELLS: 5, PID_STIMPAK: 20}
    want = [trade.Want((CELLS,), 4), trade.Want((PID_STIMPAK,), 10)]
    price = {CELLS: 1000, PID_STIMPAK: 200}
    # 2 cells carried: 2 more (2000), then 4 stimpaks of the 6 wanted (the 800 left)
    assert trade.buys(stock, {CELLS: 2, PID_STIMPAK: 4}, want, price, 2800) == [(CELLS, 2), (PID_STIMPAK, 4)]
    assert trade.buys(stock, {CELLS: 4, PID_STIMPAK: 10}, want, price, 10**6) == []


def test_the_cheapest_ammo_that_fits_goes_first() -> None:
    stock = {JHP: 3, 31: 3}
    assert trade.buys(stock, {}, [trade.Want((JHP, 31), 4)], {JHP: 90, 31: 60}, 10**6) == [(31, 3), (JHP, 1)]


def test_goods_pay_without_change() -> None:
    goods = [(RIFLE, 1, 1000), (ARMOR, 2, 300), (JHP, 5, 75)]
    assert trade.cover(goods, 1650) == [(RIFLE, 1), (ARMOR, 2)]  # 1600: caps make up the last 50
    assert trade.cover(goods, 250) == [(JHP, 3)]
    assert trade.cover(goods, 0) == []


class _Gvars:
    def __init__(self, gvars: dict[str, int], journals: int = 0) -> None:
        self.gvars, self.journals = gvars, journals

    def _count(self, pid: int) -> int:
        return self.journals if pid == trade.PID_JOURNALS else 0


def test_caps_are_kept_for_stapleton(monkeypatch) -> None:
    monkeypatch.setattr(trade.quests, "gvar", lambda a, name: a.gvars.get(name, 0))
    assert trade.reserve(_Gvars({})) == 750  # the records still for sale
    assert trade.reserve(_Gvars({"MASTER_FILLER_10": 1})) == 0
    assert trade.reserve(_Gvars({"FIND_WATER_CHIP": 2, "CHEMISTRY_BOOK": 1})) == 750
    assert trade.reserve(_Gvars({"MASTER_FILLER_10": 1, "CHEMISTRY_BOOK": 1}, journals=1)) == 0
