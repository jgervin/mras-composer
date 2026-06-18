from src.orchestrator.commands import Play, Idle, RenderAhead
from src.orchestrator.model import Round
from src.orchestrator.core import Orchestrator


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _orch(displays=("display-1", "display-2", "display-3", "display-4")):
    return Orchestrator(list(displays), clock=_Clock(), presence_ttl_s=5.0)


def test_commands_are_value_equal_and_hashable():
    assert Play("display-1", "jason", Round.OPENER, 0) == Play("display-1", "jason", Round.OPENER, 0)
    assert Idle("display-2") == Idle("display-2")
    assert RenderAhead("jason", Round.ROUND2) == RenderAhead("jason", Round.ROUND2)
    # frozen → usable in sets
    assert len({Idle("display-1"), Idle("display-1")}) == 1


def test_on_identify_starts_opener_on_all_idle_displays_and_renders_ahead():
    o = _orch()
    cmds = o.on_identify("jason")
    # opener (round OPENER, slot 0) on all four idle displays
    plays = [c for c in cmds if isinstance(c, Play)]
    assert plays == [
        Play("display-1", "jason", Round.OPENER, 0),
        Play("display-2", "jason", Round.OPENER, 0),
        Play("display-3", "jason", Round.OPENER, 0),
        Play("display-4", "jason", Round.OPENER, 0),
    ]
    # exactly one render-ahead for the round-2 pair
    assert RenderAhead("jason", Round.ROUND2) in cmds
    assert sum(isinstance(c, RenderAhead) for c in cmds) == 1


def test_clip_ended_advances_to_round2_paired_AABB():
    o = _orch()
    o.on_identify("jason")  # opener on 1..4 (all now playing)
    # all four openers end (first one advances the program to round 2)
    cmds = []
    for d in ["display-1", "display-2", "display-3", "display-4"]:
        cmds = o.on_clip_ended(d)
    # after the last clip_ended, every display projects round 2, paired A,A,B,B
    plays = {c.display: c for c in cmds if isinstance(c, Play)}
    # the last-ended display (display-4) is reassigned in this call
    assert plays["display-4"] == Play("display-4", "jason", Round.ROUND2, 1)


def test_first_opener_end_advances_program_once():
    o = _orch(displays=("display-1", "display-2"))
    o.on_identify("jason")            # opener on 1,2
    cmds1 = o.on_clip_ended("display-1")  # first → advance to round 2, play round2 on d1
    assert Play("display-1", "jason", Round.ROUND2, 0) in cmds1
    cmds2 = o.on_clip_ended("display-2")  # second → program already round2, no double-advance
    assert Play("display-2", "jason", Round.ROUND2, 1) in cmds2
