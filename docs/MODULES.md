# Action and route modules

The command entry points and public interfaces remain `f1.actions.Actor`, `f1.routes.Step`,
`f1.routes.run`, `f1.routes.all_routes` and the route dictionaries in `f1.routes` and `f1.idealist`.

## Verified actions

`Actor` owns its memory connection, presence guard and event log. Its action groups use that same instance:

| Module | Responsibility |
|---|---|
| `action_shared` | `Outcome` and `Held` records |
| `action_movement` | Walking, following a leg, camera positioning and movement waits |
| `action_interaction` | Aiming at objects, confirming hits, dialogue and skill/item interactions |
| `action_inventory` | Loot windows, inventory controls, equipment and weapon preparation |
| `action_combat` | Attacks, turn control, rearming, forced-fight policy and retreat |
| `action_ui` | Rest, movies, world-map controls, save and load screens |
| `actions` | Actor construction, public compatibility exports and action commands |

The methods retain the same signatures and action ordering. Calling one through `Actor` uses the same
memory and log as the other groups. The existing navigation, loot, weapon, save and risk modules remain
the owners of their lower-level mechanisms.

The runtime switches `ART_AIMS` and `SKIP_CINEMA` remain configurable through `f1.actions`. Shared types
are available from there too. When a test replaces an implementation dependency, patch the module which
defines the method; re-exporting the method does not change where its global names are looked up.

## Routes

| Module family | Responsibility |
|---|---|
| `route_support` | Step records, reusable predicates, travel, healing and settling a screen |
| `route_runner` | Preparation, checkpoints, recovery, retry limits and step execution |
| `agent_*` | Regional tasks and ordered step lists for the Agent routes |
| `idealist_support` | Shared operations for the Idealist routes |
| `idealist_*` | Regional tasks and ordered step lists for the Idealist routes |
| `boneyard`, `cathedral`, `military`, `raiders`, `watershed` | Existing late-game regional routes |
| `routes`, `idealist` | Compatibility exports and route registries; `routes` also keeps its command |

A regional module imports reusable operations from their owning module, rather than importing the registry
which imports that region. Keep a task's predicates, constants, dialogue identifiers and steps together.
The ordered step lists are part of the route contract: changing import structure must preserve the function
captured by each step, including names reused by another region.

The runner retries a failed check where the game stands. It reloads only after death or a stuck game, then
returns to the step after the last checkpoint. Optional steps and exploit settings retain their separate
rules. Preserve the event fields, save conventions and one-based resume index when changing the runner.

`tests/test_route_data.py` inspects regional source files as well as shared route helpers, so moved route
data remains covered by its checks. Route and action tests patch their actual implementation modules.
