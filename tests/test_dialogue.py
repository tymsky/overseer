"""Choosing a talk's options (f1/dialogue.py)."""


def test_closing_a_talk_someone_began_skips_the_rude_last_option():
    # Necropolis's Hotel Zombie Guard, an ironman run: the last options closed his talk and he attacked
    from f1 import dialogue

    first = ["Searching for the remains of the future past.", "I'm an explorer.", "What's it to you, wormface?"]
    second = ["Oh, yeah.", "I'm gonna take that face of yours and shove it up yer . . ."]
    assert dialogue.closing(first) == 0 and dialogue.closing(second) == 0
    assert dialogue.closing(["Tell me more.", "[Done]"]) == 1
    assert dialogue.closing(["Thanks.", "Goodbye.", "[Done]"]) == 1
