from src.orchestrator.model import Round, next_round


def test_next_round_advances_opener_to_round2_to_done():
    assert next_round(Round.OPENER) == Round.ROUND2
    assert next_round(Round.ROUND2) == Round.DONE
    assert next_round(Round.DONE) == Round.DONE  # terminal, never past done
