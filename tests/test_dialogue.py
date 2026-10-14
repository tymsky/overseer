"""Choosing a talk's options (f1/dialogue.py)."""


def test_closing_a_talk_someone_began_skips_the_rude_last_option():
    # Necropolis's Hotel Zombie Guard, an ironman run: the last options closed his talk and he attacked
    from f1 import dialogue

    first = ["an answer", "another answer", f"a retort, {dialogue.RUDE[0]}"]
    second = ["a short answer", f"a threat to {dialogue.RUDE[2]}"]
    assert dialogue.closing(first) == 0 and dialogue.closing(second) == 0
    assert dialogue.closing(["a question", "[Done]"]) == 1
    assert dialogue.closing(["thanks", "goodbye then", "[Done]"]) == 1


def test_closing_never_picks_the_option_that_runs_the_combat_node():
    # Necropolis's Harry, a scout: the sneak failed, the lie too; the last option ran harrycbt and began a fight
    from f1 import dialogue

    caught = ["an answer", "a confession, then a threat"]
    assert dialogue.closing(caught) == 1 and dialogue.closing(caught, frozenset({1})) == 0
    assert dialogue.closing(["a challenge", "[Done]"], frozenset({0, 1})) == 1  # nothing else offered: as before


def test_a_scripts_combat_node_is_known_by_its_procedure_name(monkeypatch):
    from f1 import scripts

    monkeypatch.setattr(scripts, "procedure_names", lambda s: ("", "start", "harrycbt", "harry03", "combat_p_proc"))
    assert scripts.starts_fight("harry", 2) and not scripts.starts_fight("harry", 3)
    assert not scripts.starts_fight("harry", 0) and not scripts.starts_fight("harry", 9)
    assert not scripts.starts_fight("", 2)
