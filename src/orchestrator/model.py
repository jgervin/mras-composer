import math
from enum import IntEnum


class Round(IntEnum):
    OPENER = 0
    ROUND2 = 1
    DONE = 2


def next_round(r: Round) -> Round:
    """opener → round 2 → done (terminal). No round 3 — the cap is structural."""
    return Round.DONE if r >= Round.ROUND2 else Round(r + 1)


def even_split(active_newest_first: list[str], displays: list[str]) -> dict[str, str]:
    """Map each display → owner uuid. Displays divide as evenly as possible
    among the active people; the newest people get any remainder, and when
    active people outnumber displays only the newest len(displays) are served
    (one display each)."""
    d, a = len(displays), len(active_newest_first)
    if a == 0 or d == 0:
        return {}
    base, rem = divmod(d, a)
    owners: list[str] = []
    for i, uuid in enumerate(active_newest_first):
        owners.extend([uuid] * (base + (1 if i < rem else 0)))
    return {displays[i]: owners[i] for i in range(min(d, len(owners)))}


def pair_slot(display: str, owned_displays: list[str]) -> int:
    """Round-2 pairing: split an owner's displays into two contiguous groups —
    the first ceil(n/2) show ad A (slot 0), the rest show ad B (slot 1).
    n=1→[0], n=2→[0,1], n=3→[0,0,1], n=4→[0,0,1,1]."""
    half = math.ceil(len(owned_displays) / 2)
    return 0 if owned_displays.index(display) < half else 1
