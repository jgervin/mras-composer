import time
from dataclasses import dataclass

from src.orchestrator.commands import Idle, Play, RenderAhead
from src.orchestrator.model import Round, even_split, next_round, pair_slot


@dataclass
class _Program:
    uuid: str
    first_seen: float
    round: Round = Round.OPENER


@dataclass
class _Screen:
    owner: str | None = None
    round: Round | None = None
    playing: bool = False


class Orchestrator:
    def __init__(self, displays: list[str], clock=time.monotonic,
                 presence_ttl_s: float = 5.0) -> None:
        self._displays = list(displays)
        self._clock = clock
        self._ttl = presence_ttl_s
        self._programs: dict[str, _Program] = {}
        self._present: dict[str, float] = {}
        self._screens: dict[str, _Screen] = {d: _Screen() for d in self._displays}

    # ---- event handlers (each returns a list of Command) ----

    def on_identify(self, uuid: str) -> list:
        now = self._clock()
        prog = self._programs.get(uuid)
        if prog is None or prog.round == Round.DONE:
            self._programs[uuid] = _Program(uuid, first_seen=now)
        self._present[uuid] = now
        return self._reassign()

    def on_clip_ended(self, display: str) -> list:
        sc = self._screens[display]
        sc.playing = False
        owner = sc.owner
        if owner is not None and owner in self._programs \
                and self._programs[owner].round == sc.round:
            # first display of this owner to finish the current round → advance
            self._programs[owner].round = next_round(self._programs[owner].round)
        return self._reassign()

    # ---- internals ----

    def _active_newest_first(self) -> list[str]:
        active = [u for u, p in self._programs.items()
                  if p.round != Round.DONE and u in self._present]
        return sorted(active, key=lambda u: self._programs[u].first_seen, reverse=True)

    def _reassign(self) -> list:
        split = even_split(self._active_newest_first(), self._displays)
        owned: dict[str, list[str]] = {}
        for disp, owner in split.items():
            owned.setdefault(owner, []).append(disp)
        cmds: list = []
        render_ahead_owners: list[str] = []  # one render-ahead per owner, not per display
        for disp in self._displays:
            sc = self._screens[disp]
            if sc.playing:
                continue  # never interrupt a personalized clip mid-play
            new_owner = split.get(disp)
            if new_owner is None:
                if sc.owner is not None:
                    sc.owner, sc.round = None, None
                    cmds.append(Idle(disp))
                continue
            rnd = self._programs[new_owner].round
            # Opener plays one shared render on every owned display (slot 0);
            # only round 2 splits into the A/B pair.
            slot = pair_slot(disp, sorted(owned[new_owner])) if rnd == Round.ROUND2 else 0
            sc.owner, sc.round, sc.playing = new_owner, rnd, True
            cmds.append(Play(disp, new_owner, rnd, slot))
            if rnd == Round.OPENER and new_owner not in render_ahead_owners:
                render_ahead_owners.append(new_owner)
        cmds.extend(RenderAhead(o, Round.ROUND2) for o in render_ahead_owners)
        return cmds
