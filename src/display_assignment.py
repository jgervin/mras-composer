"""Display assignment for per-display personalized ads (T-C).

Splits the connected kiosk displays evenly among the people currently in
frame (`faces_in_frame` from the vision trigger): alone -> every free
display, two people -> half each, more people than displays -> one each.
Short-lived reservations (TTL ~ clip length) stop near-simultaneous
triggers from claiming the same screen; an expired reservation frees the
display automatically, so there is no cleanup job.
"""


class DisplayAssigner:
    def __init__(self) -> None:
        self._reserved_until: dict[str, float] = {}

    def assign(
        self,
        screen_ids: list[str],
        faces_in_frame: int,
        now: float,
        hold_secs: float,
    ) -> list[str]:
        free = [s for s in screen_ids if self._reserved_until.get(s, 0.0) <= now]
        if not free:
            return []
        per_person = max(1, len(screen_ids) // max(1, faces_in_frame))
        taken = free[:per_person]
        for screen_id in taken:
            self._reserved_until[screen_id] = now + hold_secs
        return taken
