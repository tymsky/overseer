"""Best reachable ending predicates, checked against the engine's slideshow conditions."""

from f1 import quests

VARIABLES = (
    "MASTER_BLOWN",
    "VATS_BLOWN",
    "NECROP_WATER_CHIP_TAKEN",
    "NECROP_WATER_PUMP_FIXED",
    "NECROPOLIS_INVADED",
    "FOLLOWERS_INVADED",
    "ARADESH_STATUS",
    "TANDI_STATUS",
    "SHADY_SANDS_INVADED",
    "CAPTURE_GIZMO",
    "KILLIAN_DEAD",
    "JUNKTOWN_INVADED",
    "BECOME_AN_INITIATE",
    "ENEMY_BROTHERHOOD",
    "HUB_INVADED",
    "RAIDERS",
    "GARL_DEAD",
    "TOTAL_RAIDERS",
)


def evaluate(values: dict[str, int], *, final: bool = True) -> dict:
    """No bonus skill check substitutes for these town and main-quest predicates."""
    get = values.__getitem__
    checks = {
        "necropolis": bool(get("NECROP_WATER_CHIP_TAKEN"))
        and get("NECROP_WATER_PUMP_FIXED") == 2
        and not get("NECROPOLIS_INVADED"),
        "followers": not get("FOLLOWERS_INVADED"),
        "shady_sands": get("ARADESH_STATUS") == 0 and get("TANDI_STATUS") in (0, 2) and not get("SHADY_SANDS_INVADED"),
        "junktown": get("CAPTURE_GIZMO") == 2 and not get("KILLIAN_DEAD") and not get("JUNKTOWN_INVADED"),
        "brotherhood": not (get("BECOME_AN_INITIATE") == 2 and get("ENEMY_BROTHERHOOD")),
        "hub": not get("HUB_INVADED"),
        "raiders": get("RAIDERS") >= 2
        and (bool(get("GARL_DEAD")) and get("TOTAL_RAIDERS") < 8 or get("TOTAL_RAIDERS") < 4),
    }
    if final:
        checks["main_quests"] = bool(get("MASTER_BLOWN")) and bool(get("VATS_BLOWN"))
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "missing": [key for key, ok in checks.items() if not ok],
        "values": dict(values),
    }


def audit(actor, *, final: bool = True) -> dict:
    values = {key: quests.gvar(actor, key) for key in VARIABLES}
    result = evaluate(values, final=final)
    pending = {key: row for key, row in getattr(actor, "pending_objectives", {}).items() if row["required"]}
    if pending:
        result["checks"]["deferred_objectives"] = False
        result["missing"].append("deferred_objectives")
        result["pending"] = pending
        result["ok"] = False
    actor.log.emit("goal_audit", **result, final=final)
    return result
