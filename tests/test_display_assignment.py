from src.display_assignment import DisplayAssigner

SCREENS = ["display-1", "display-2", "display-3", "display-4"]


def test_alone_claims_every_free_display():
    a = DisplayAssigner()
    assert a.assign(SCREENS, faces_in_frame=1, now=100.0, hold_secs=12) == SCREENS


def test_two_faces_split_the_displays_evenly():
    a = DisplayAssigner()
    first = a.assign(SCREENS, faces_in_frame=2, now=100.0, hold_secs=12)
    second = a.assign(SCREENS, faces_in_frame=2, now=100.5, hold_secs=12)
    assert len(first) == 2 and len(second) == 2
    assert set(first).isdisjoint(second)  # reservations prevent collisions


def test_reservation_expires_and_frees_the_display():
    a = DisplayAssigner()
    a.assign(SCREENS, faces_in_frame=1, now=100.0, hold_secs=12)
    assert a.assign(SCREENS, faces_in_frame=1, now=105.0, hold_secs=12) == []
    assert a.assign(SCREENS, faces_in_frame=1, now=113.0, hold_secs=12) == SCREENS


def test_more_faces_than_displays_still_serves_one_each():
    a = DisplayAssigner()
    served = [a.assign(SCREENS, faces_in_frame=6, now=100.0, hold_secs=12) for _ in range(4)]
    assert all(len(s) == 1 for s in served)
    assert a.assign(SCREENS, faces_in_frame=6, now=100.0, hold_secs=12) == []  # all busy


def test_partial_availability_takes_only_free_displays():
    a = DisplayAssigner()
    a.assign(SCREENS[:1], faces_in_frame=1, now=100.0, hold_secs=12)  # display-1 busy
    got = a.assign(SCREENS, faces_in_frame=2, now=101.0, hold_secs=12)
    assert "display-1" not in got and len(got) == 2
