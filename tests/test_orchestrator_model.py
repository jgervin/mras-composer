from src.orchestrator.model import Round, next_round
from src.orchestrator.model import even_split  # noqa: E402
from src.orchestrator.model import pair_slot  # noqa: E402


def test_next_round_advances_opener_to_round2_to_done():
    assert next_round(Round.OPENER) == Round.ROUND2
    assert next_round(Round.ROUND2) == Round.DONE
    assert next_round(Round.DONE) == Round.DONE  # terminal, never past done


def test_even_split_solo_owns_all_displays():
    d = ["display-1", "display-2", "display-3", "display-4"]
    assert even_split(["jason"], d) == {dd: "jason" for dd in d}


def test_even_split_two_people_split_evenly_newest_first():
    d = ["display-1", "display-2", "display-3", "display-4"]
    # newest-first order: maria is newest
    assert even_split(["maria", "jason"], d) == {
        "display-1": "maria", "display-2": "maria",
        "display-3": "jason", "display-4": "jason",
    }


def test_even_split_remainder_goes_to_newest():
    d = ["display-1", "display-2", "display-3", "display-4"]
    # 3 active, 4 displays → newest gets the extra (2), others 1 each
    assert even_split(["c", "b", "a"], d) == {
        "display-1": "c", "display-2": "c",
        "display-3": "b", "display-4": "a",
    }


def test_even_split_more_people_than_displays_newest_win_one_each():
    d = ["display-1", "display-2"]
    assert even_split(["d", "c", "b", "a"], d) == {
        "display-1": "d", "display-2": "c",  # only the 2 newest are served
    }


def test_even_split_empty_active_is_empty():
    assert even_split([], ["display-1"]) == {}


def test_pair_slot_four_displays_is_AABB():
    owned = ["display-1", "display-2", "display-3", "display-4"]
    assert [pair_slot(dd, owned) for dd in owned] == [0, 0, 1, 1]


def test_pair_slot_two_displays_is_AB():
    owned = ["display-1", "display-2"]
    assert [pair_slot(dd, owned) for dd in owned] == [0, 1]


def test_pair_slot_one_display_is_A():
    owned = ["display-1"]
    assert pair_slot("display-1", owned) == 0


def test_pair_slot_three_displays_is_AAB():
    owned = ["display-1", "display-2", "display-3"]
    assert [pair_slot(dd, owned) for dd in owned] == [0, 0, 1]
