"""f1.play's chains name routes that exist, for characters that exist."""

from f1 import chargen, play, routes


def test_every_chain_names_known_routes_of_a_known_character() -> None:
    known = routes.all_routes()
    for character, chain in play.CHAINS.items():
        assert character in chargen.BUILDS
        assert [name for name in chain if name not in known] == []
        assert len(set(chain)) == len(chain)
