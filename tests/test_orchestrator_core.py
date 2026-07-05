from src.orchestrator.commands import EvictRender, Play, Idle, RenderAhead
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


def test_clip_ended_peels_round2_back_to_half():
    # Peel-back (TODO-10): after the openers end on all four displays, round 2
    # continues on only the first floor(4/2)=2 displays (A on display-1, B on
    # display-2); the dropped half idles.
    o = _orch()
    o.on_identify("jason")  # opener on 1..4 (all now playing)
    all_cmds = []
    for d in ["display-1", "display-2", "display-3", "display-4"]:
        all_cmds += o.on_clip_ended(d)
    plays = {c.display: c for c in all_cmds if isinstance(c, Play)}
    idled = {c.display for c in all_cmds if isinstance(c, Idle)}
    # kept half continues round 2, paired A,B
    assert plays["display-1"] == Play("display-1", "jason", Round.ROUND2, 0)
    assert plays["display-2"] == Play("display-2", "jason", Round.ROUND2, 1)
    # dropped half idles, never gets a round-2 play
    assert "display-3" not in plays and "display-4" not in plays
    assert "display-3" in idled and "display-4" in idled


def test_full_peelback_four_to_two_survives_presence_loss():
    # Owner-locked headline scenario (2026-07-05), the E2E in miniature: opener on
    # all four displays → subject walks away (presence expires) → round 2 still
    # peels to EXACTLY two displays; the other two idle.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1", "display-2", "display-3", "display-4"],
                     clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")               # opener on all 4 (playing)
    clock.t = 100.0                      # long gone: presence expired (still < 900s abandon)
    o.tick()
    assert "jason" not in o._present
    all_cmds = []
    for d in ["display-1", "display-2", "display-3", "display-4"]:
        all_cmds += o.on_clip_ended(d)
    round2 = [c for c in all_cmds if isinstance(c, Play) and c.round == Round.ROUND2]
    idled = {c.display for c in all_cmds if isinstance(c, Idle)}
    assert len(round2) == 2                                    # exactly half
    assert {c.display for c in round2} == {"display-1", "display-2"}
    assert {"display-3", "display-4"} <= idled                # dropped half idles


def test_first_opener_end_advances_once_then_peels_to_one():
    o = _orch(displays=("display-1", "display-2"))
    o.on_identify("jason")            # opener on 1,2
    cmds1 = o.on_clip_ended("display-1")  # first → advance to round 2; peel keeps display-1
    assert Play("display-1", "jason", Round.ROUND2, 0) in cmds1
    cmds2 = o.on_clip_ended("display-2")  # second → already round2 (no double-advance); dropped half
    assert o._programs["jason"].round == Round.ROUND2   # still round 2, NOT past-done
    assert Idle("display-2") in cmds2
    assert not any(isinstance(c, Play) for c in cmds2)  # display-2 dropped, no round-2 play


def test_program_caps_at_round2_then_idles_no_round3():
    o = _orch(displays=("display-1",))
    o.on_identify("jason")                 # opener on d1
    o.on_clip_ended("display-1")           # → round 2 on d1
    cmds = o.on_clip_ended("display-1")    # round 2 ends → DONE → idle (NOT round 3)
    assert Idle("display-1") in cmds
    assert not any(isinstance(c, Play) for c in cmds)  # no round 3 play


def test_program_done_evicts_owner_renders_but_mid_program_does_not():
    # issue #27: the DONE boundary must tell the runtime to drop the owner's
    # cached renders; mid-program round advances must NOT (round 2 reuses the
    # render-ahead cache by design).
    o = _orch(displays=("display-1",))
    o.on_identify("jason")                       # opener on d1
    mid = o.on_clip_ended("display-1")           # → round 2 (mid-program)
    assert not any(isinstance(c, EvictRender) for c in mid)
    done = o.on_clip_ended("display-1")          # round 2 ends → DONE
    assert EvictRender("jason") in done


def test_clip_ended_for_unknown_display_returns_empty():
    # A kiosk connecting with a screen_id outside the configured displays must not
    # crash the handler — on_clip_ended returns [] rather than raising KeyError.
    o = _orch()
    assert o.on_clip_ended("display-99") == []


def test_new_identify_does_not_interrupt_playing_clips():
    o = _orch()
    o.on_identify("jason")           # jason opener on 1..4 (all playing)
    cmds = o.on_identify("maria")    # maria identified while jason mid-clip
    # nothing plays yet — all four are still playing jason's opener
    assert not any(isinstance(c, Play) for c in cmds)


def test_newest_wins_takes_freed_displays_at_boundaries():
    o = _orch()
    o.on_identify("jason")
    o.on_identify("maria")           # 2 active → split 2/2, maria newest
    # display-3 frees: maria (newest) owns 3,4 → her opener on the freed display-3
    cmds = o.on_clip_ended("display-3")
    play = next(c for c in cmds if isinstance(c, Play))
    assert play.owner == "maria"
    assert play.round == Round.OPENER


def test_round2_plays_even_after_presence_expires():
    # Peel-back contract (owner-locked 2026-07-05): once a program starts it runs
    # opener → round 2 → done WHETHER OR NOT the subject is still present. Presence
    # no longer gates round advancement — the subject can walk away and round 2
    # still plays.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")               # present at t=0, opener on d1 (playing)
    clock.t = 3.0
    o.on_presence(["jason"])             # heartbeat refreshes last_seen=3.0
    clock.t = 10.0                       # >5s since last heartbeat → presence expired
    o.tick()                             # jason no longer present; d1 still playing → skipped
    assert "jason" not in o._present     # precondition: presence is gone
    cmds = o.on_clip_ended("display-1")  # clip ends → advances to round 2 and PLAYS (not idle)
    assert Play("display-1", "jason", Round.ROUND2, 0) in cmds
    assert Idle("display-1") not in cmds


def test_presence_heartbeat_keeps_owner_active():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")
    clock.t = 4.0
    o.on_presence(["jason"])             # refresh
    clock.t = 6.0                        # only 2s since refresh → still present
    o.tick()
    cmds = o.on_clip_ended("display-1")  # ends opener → advances to round 2, still jason
    assert any(isinstance(c, Play) and c.owner == "jason" for c in cmds)


def test_departed_subject_keeps_share_until_done_newest_takes_freed():
    # New peel-back contract: presence no longer drops a running program. A subject
    # who walks away keeps their even-split share of displays until their own
    # program completes; the newest present subject takes displays as they FREE
    # (via even_split), rather than stealing the departed subject's in-flight one.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1", "display-2"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")    # t0: jason owns 1,2 (opener playing on both)
    clock.t = 1.0
    o.on_identify("maria")    # maria newest; even_split → d1:maria, d2:jason (deferred, busy)
    clock.t = 9.0
    o.on_presence(["maria"])  # maria fresh; jason silent since t=0
    clock.t = 10.0
    o.tick()                  # jason presence expired BUT program alive (<900s abandon)
    c1 = o.on_clip_ended("display-1")   # d1 frees → maria (newest) takes it for her opener
    assert any(isinstance(c, Play) and c.owner == "maria" for c in c1)
    c2 = o.on_clip_ended("display-2")   # d2 frees → jason's OWN round 2 plays (he left, runs on)
    assert Play("display-2", "jason", Round.ROUND2, 0) in c2


# ---- abandoned-program TTL sweep (issue #36) ----


def test_abandoned_mid_round_program_swept_after_ttl_fresh_opener_on_return():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    clock.t = 901.0                      # 901 > 900 → abandoned
    cmds = o.tick()
    assert EvictRender("jason") in cmds
    assert "jason" not in o._programs    # program forgotten
    o.on_clip_ended("display-1")         # stale clip ends → display idles
    clock.t = 902.0
    cmds = o.on_identify("jason")        # returning visitor → fresh program
    assert Play("display-1", "jason", Round.OPENER, 0) in cmds
    assert o._programs["jason"].first_seen == 902.0


def test_return_within_ttl_restamps_abandon_window_no_evict():
    # A subject who leaves mid-program and returns within the abandon window keeps
    # the SAME program (no fresh opener, no evict) and re-stamps the window so it is
    # measured from the return, not the original identify.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    o.on_clip_ended("display-1")         # opener ends → program advances to ROUND2
    assert o._programs["jason"].round == Round.ROUND2
    clock.t = 7.0                        # jason's presence stale (>5s), program NOT swept (<900s)
    o.tick()
    clock.t = 600.0                      # returns within the 900s window
    cmds = o.on_identify("jason")
    assert not any(isinstance(c, EvictRender) for c in cmds)
    assert o._programs["jason"].round == Round.ROUND2   # same program, not a fresh opener
    assert o._programs["jason"].last_present == 600.0   # window re-stamped to the return
    # leave again → fresh full window measured from t=600, not t=0
    clock.t = 1499.0                     # 899s since last_present → still safe
    cmds = o.tick()
    assert not any(isinstance(c, EvictRender) for c in cmds)
    assert "jason" in o._programs


def test_return_after_sweep_gets_fresh_opener():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    clock.t = 901.0
    o.tick()                             # sweep: program forgotten
    o.on_clip_ended("display-1")         # stale clip ends → display idles
    clock.t = 960.0
    cmds = o.on_identify("jason")        # return after the sweep
    assert Play("display-1", "jason", Round.OPENER, 0) in cmds
    assert o._programs["jason"].round == Round.OPENER


def test_abandon_ttl_boundary_exactly_900_does_not_expire():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)  # default TTL 900
    o.on_identify("jason")
    assert o._programs["jason"].last_present == 0.0  # initialized to first_seen
    clock.t = 900.0                      # exactly the TTL → strict >, NOT expired
    cmds = o.tick()
    assert not any(isinstance(c, EvictRender) for c in cmds)
    assert "jason" in o._programs


def test_heartbeating_person_never_expires_across_20_minutes():
    # Highest-value test: catches a missing last_present re-stamp in on_presence.
    # Heartbeat every 60s, then tick 10s later while the presence TTL (5s) has the
    # person OUT of _present — survival then depends solely on last_present.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")
    evicts = []
    for k in range(1, 21):               # 20 simulated minutes
        clock.t = 60.0 * k
        evicts += [c for c in o.on_presence(["jason"]) if isinstance(c, EvictRender)]
        clock.t = 60.0 * k + 10.0        # presence stale; only last_present protects
        evicts += [c for c in o.tick() if isinstance(c, EvictRender)]
    assert evicts == []
    assert "jason" in o._programs


def test_done_program_swept_after_ttl_without_second_evict():
    # DONE entries were already evicted at their boundary (issue #27); the sweep
    # must GC the leaked dict entry WITHOUT a duplicate EvictRender.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")
    o.on_clip_ended("display-1")         # → ROUND2
    done = o.on_clip_ended("display-1")  # → DONE (EvictRender at the boundary)
    assert EvictRender("jason") in done
    clock.t = 901.0
    cmds = o.tick()
    assert "jason" not in o._programs    # leaked DONE entry GC'd
    assert not any(isinstance(c, EvictRender) for c in cmds)  # no duplicate


def test_abandon_ttl_policy_is_read_at_eval_time():
    # Flipping the policy value between ticks must change behavior WITHOUT
    # rebuilding the Orchestrator (env/config is read through the callable).
    clock = _Clock(0.0)
    ttl = {"value": 900.0}
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: ttl["value"])
    o.on_identify("jason")
    clock.t = 100.0
    cmds = o.tick()                      # 100 < 900 → intact
    assert not any(isinstance(c, EvictRender) for c in cmds)
    ttl["value"] = 10.0                  # provider flips 900 → 10
    cmds = o.tick()                      # 100 > 10 → swept on the very next tick
    assert EvictRender("jason") in cmds
    assert "jason" not in o._programs


def test_sweep_is_per_subject_only_silent_person_evicted():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1", "display-2"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0, then silent forever
    clock.t = 1.0
    o.on_identify("maria")
    for k in range(1, 4):                # maria heartbeats at t=300/600/900
        clock.t = 300.0 * k
        o.on_presence(["maria"])
    clock.t = 901.5                      # jason: 901.5s silent; maria: 1.5s
    cmds = o.tick()
    evicts = [c for c in cmds if isinstance(c, EvictRender)]
    assert evicts == [EvictRender("jason")]
    assert "jason" not in o._programs
    assert "maria" in o._programs
