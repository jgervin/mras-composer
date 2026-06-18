from enum import IntEnum


class Round(IntEnum):
    OPENER = 0
    ROUND2 = 1
    DONE = 2


def next_round(r: Round) -> Round:
    """opener → round 2 → done (terminal). No round 3 — the cap is structural."""
    return Round.DONE if r >= Round.ROUND2 else Round(r + 1)
