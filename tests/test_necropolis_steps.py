"""Necropolis' route steps whose failure stands (idealist_necropolis.IDEALIST_NECROPOLIS)."""

from f1 import idealist_necropolis


def test_the_pump_failing_on_a_destroyed_junk_does_not_end_the_route() -> None:
    """The measured case (an ironman run): one plain failure at the pump, then a critical failure destroyed the only
    Junk; the step failed again with nothing changed and the route ended "no progress". The roll's outcome stands."""
    step = next(s for s in idealist_necropolis.IDEALIST_NECROPOLIS if s.name == "the pump fixed")
    assert step.optional
