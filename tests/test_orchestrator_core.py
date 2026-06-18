from src.orchestrator.commands import Play, Idle, RenderAhead
from src.orchestrator.model import Round


def test_commands_are_value_equal_and_hashable():
    assert Play("display-1", "jason", Round.OPENER, 0) == Play("display-1", "jason", Round.OPENER, 0)
    assert Idle("display-2") == Idle("display-2")
    assert RenderAhead("jason", Round.ROUND2) == RenderAhead("jason", Round.ROUND2)
    # frozen → usable in sets
    assert len({Idle("display-1"), Idle("display-1")}) == 1
