from dataclasses import dataclass

from src.orchestrator.model import Round


@dataclass(frozen=True)
class Play:
    """Start the owner's current round on a display. pair_slot is 0/1 for the
    round-2 A/B pairing; ignored (always 0) for the opener."""
    display: str
    owner: str
    round: Round
    pair_slot: int
    # Triggering camera screen_id (for render-lane scope on a cache-miss render).
    screen_id: str | None = None


@dataclass(frozen=True)
class Idle:
    """Return a display to the standard idle shuffle."""
    display: str


@dataclass(frozen=True)
class RenderAhead:
    """Pre-render an owner's upcoming round while the current one plays."""
    owner: str
    round: Round
    # Triggering camera screen_id (for render-lane scope).
    screen_id: str | None = None
