"""Per-frame confirmation: the frame captured is the frame that was asked for.

Run: python3 -m pytest tests/test_frame_confirm.py

This is deliberately built to fail the way the old code failed. Every automated
test here used to pass while real scans came back empty, because the tests held
each pattern for longer than FPP needed and then FABRICATED the detection —
never once exercising the gap between "FPP accepted the command" and "the LEDs
changed". So the fake string below models exactly that gap: a push can be
dropped, and until a later push takes, the string keeps showing the PREVIOUS
pattern while every command returns successfully.

Against that string, a scan driven by a fixed timer captures frame N while the
string still shows frame N-1 and reports success. The assertion that matters
here is `seen == asked`: for every frame the phone confirmed, the pattern the
string was actually showing is the pattern the server drove for that frame.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import blinkymap_server as B  # noqa: E402

PIXELS = 24


# ── Pattern-change expectation ────────────────────────────────────────────────

def test_change_from_dark_is_total():
    dark = "000000" * PIXELS
    first = B.coded_frame_pattern(PIXELS, 0, B.coded_digit_count(PIXELS))
    assert B.coded_pattern_change(dark, first) == 1.0


def test_identical_patterns_change_nothing():
    p = B.coded_frame_pattern(PIXELS, 1, B.coded_digit_count(PIXELS))
    assert B.coded_pattern_change(p, p) == 0.0


def test_no_previous_pattern_counts_as_a_full_change():
    p = B.coded_frame_pattern(PIXELS, 0, B.coded_digit_count(PIXELS))
    assert B.coded_pattern_change(None, p) == 1.0


def test_real_scan_transitions_are_all_visible():
    """No frame of a real scan may be waved through on stability alone.

    `expect_change` is what tells the phone it is allowed to demand a visible
    change before believing a frame. If a transition fell below the threshold
    the phone would confirm the frame the moment the picture stopped moving —
    which is exactly the failure mode the handshake exists to remove — so the
    property is asserted across the pixel counts the plugin actually sees.
    """
    for total in (24, 50, 100, 500):
        digits = B.coded_digit_count(total)
        prev = "000000" * total
        for f in range(digits):
            pattern = B.coded_frame_pattern(total, f, digits)
            changed = B.coded_pattern_change(prev, pattern)
            assert changed >= B.CODED_EXPECT_CHANGE_FRAC, (
                f"{total} pixels, frame {f}: only {changed:.0%} of pixels change")
            prev = pattern


# ── A string that lies about having latched ───────────────────────────────────

class FakeString:
    """Pixel output whose commands succeed before the pixels change.

    `deaf` holds the 1-based ordinals of set_pattern calls to accept and ignore,
    which is what a dropped or too-early command looks like from the server: the
    call returns, nothing on the string changes. Push 1 is the dark reference,
    push 2 the first coded frame, and a retry costs a push of its own.
    """

    def __init__(self, deaf=(), dead=False):
        self.shown = None
        self.pushes = 0
        self.deaf = set(deaf)
        self.dead = dead

    def set_pattern(self, pattern_hex, pixel_count):
        self.pushes += 1
        if self.dead or self.pushes in self.deaf:
            return
        self.shown = pattern_hex

    def set_dark(self, pixel_count):
        self.set_pattern("000000" * pixel_count, pixel_count)

    def all_off(self):
        self.shown = None

    def release(self):
        pass

    def close(self):
        pass


class FakeSensor:
    """A phone that judges a frame the way sensor.js does: by watching.

    It never learns which pattern it was supposed to see — it only knows what
    the string is showing and whether that differs from the last frame it
    confirmed, which is precisely what a camera can determine.
    """

    def __init__(self, server, string):
        self.server = server
        self.string = string
        self.last_confirmed = None
        self.seen = []          # (frame index, pattern the string was showing)
        self.attempts = {}

    async def send(self, data):
        msg = json.loads(data)
        t = msg.get("type")
        if t in ("coded_frame", "coded_dark"):
            asyncio.get_running_loop().create_task(self._answer(msg))
        elif t == "coded_analyze":
            asyncio.get_running_loop().create_task(self._analyze())

    async def _answer(self, msg):
        await asyncio.sleep(0)
        idx = msg["index"]
        self.attempts[idx] = self.attempts.get(idx, 0) + 1
        shown = self.string.shown
        if msg.get("expect_dark"):
            confirmed = shown is None or set(shown) <= {"0"}
        elif msg.get("expect_change"):
            confirmed = shown is not None and shown != self.last_confirmed
        else:
            confirmed = True
        if confirmed:
            self.last_confirmed = shown
            self.seen.append((idx, shown))
        await self.server._handle_message(self, json.dumps({
            "type": "coded_frame_captured", "index": idx,
            "confirmed": confirmed, "settled": True, "settle_ms": 120,
            "change_pct": 100.0 if confirmed else 0.0, "lit_pct": 30.0,
            "reason": "" if confirmed else "no change seen",
        }))

    async def _analyze(self):
        await self.server._handle_message(self, json.dumps({
            "type": "coded_detections", "detections": {}, "diag": None,
        }))


def run_scan(deaf=(), dead=False, attempts=None, lit=False):
    """Drive one real _run_coded_scan against a fake string and phone."""
    async def go():
        server = B.BlinkyServer()
        server.config.pixel_count = PIXELS
        server.config.inter_pixel_delay = 0.0
        server.model.pixel_count = PIXELS
        server.current_session = B.SessionConfig(
            session_id=1, angle_deg=0.0, distance_m=2.0, height_m=1.5,
            device_pitch_deg=15.0)
        server.model.add_session(server.current_session)

        string = FakeString(deaf=deaf, dead=dead)
        if lit:
            # The aim light is still on and the server does not know it — the
            # desync the dark reference exists to survive. A reference frame
            # taken now would mask the prop itself.
            string.shown = "FFFFFF" * PIXELS
        sensor = FakeSensor(server, string)
        server.clients[sensor] = "sensor"
        sent = []

        original_broadcast = server.broadcast

        async def spy(msg, role=None):
            sent.append(msg)
            await original_broadcast(msg, role)

        server.broadcast = spy
        B._make_output = lambda cfg: string
        # A confirmed handshake makes the blind hold pointless in a test; the
        # real one keeps it because a real string has never latched faster.
        B.CODED_SETTLE_SEC = 0.0
        await server._run_coded_scan()
        return string, sensor, sent

    if attempts is not None:
        B.CODED_FRAME_ATTEMPTS = attempts
    saved_settle, saved_attempts = B.CODED_SETTLE_SEC, B.CODED_FRAME_ATTEMPTS
    try:
        return asyncio.run(go())
    finally:
        B.CODED_SETTLE_SEC, B.CODED_FRAME_ATTEMPTS = saved_settle, saved_attempts


def _asked_patterns():
    digits = B.coded_digit_count(PIXELS)
    return [B.coded_frame_pattern(PIXELS, f, digits) for f in range(digits)]


def test_clean_string_captures_every_frame_in_order():
    string, sensor, sent = run_scan()
    frames = [(i, p) for i, p in sensor.seen if i >= 0]
    assert [p for _, p in frames] == _asked_patterns()
    assert not [m for m in sent if m["type"] == "scan_aborted"]


def test_dropped_command_is_retried_rather_than_captured_stale():
    """The case a fixed timer gets wrong.

    The first pattern push is swallowed, so at the moment a timer-driven scan
    would sample, the string is still showing the dark reference. The phone
    reports no change, the server pushes again, and the frame that finally gets
    captured is the one that was asked for.
    """
    string, sensor, sent = run_scan(deaf={2})   # push 2 is frame 0
    frames = [(i, p) for i, p in sensor.seen if i >= 0]
    assert [p for _, p in frames] == _asked_patterns()
    assert sensor.attempts[0] == 2, "frame 0 should have been asked for twice"
    progress = [m for m in sent if m["type"] == "progress"]
    assert progress[0]["attempts"] == 2
    assert all(m["confirmed"] for m in progress)


def test_dropped_dark_reference_is_retried():
    """A reference frame taken while the string is lit masks the prop itself.

    Worse than having no mask at all, so the dark frame is confirmed too — but
    only ever costs the mask, never the scan: if it cannot be confirmed the scan
    goes ahead unmasked and says so.
    """
    string, sensor, sent = run_scan(deaf={1}, lit=True)   # push 1 is the dark frame
    assert sensor.attempts[-1] == 2
    assert [p for i, p in sensor.seen if i >= 0] == _asked_patterns()
    assert not [m for m in sent if m["type"] == "scan_aborted"]


def test_unconfirmable_dark_reference_costs_the_mask_not_the_scan():
    string, sensor, sent = run_scan(deaf={1, 2}, lit=True)
    assert sensor.attempts[-1] == 2, "the dark frame gets two tries, not more"
    warned = [m for m in sent
              if m["type"] == "status" and "without a mask" in m["message"]]
    assert warned, "an unmasked scan has to say so"
    assert [p for i, p in sensor.seen if i >= 0] == _asked_patterns()
    assert not [m for m in sent if m["type"] == "scan_aborted"]


def test_dead_string_aborts_instead_of_resolving_garbage():
    string, sensor, sent = run_scan(dead=True, attempts=2)
    aborted = [m for m in sent if m["type"] == "scan_aborted"]
    assert aborted, "a string that never changes must stop the scan"
    assert "never appeared" in aborted[0]["message"]
    assert not [m for m in sent if m["type"] == "scan_complete"]
    assert not [(i, p) for i, p in sensor.seen if i >= 0]


def test_abort_leaves_no_detections_behind():
    """Nothing downstream can tell a stale-frame model from a good one.

    So the scan has to leave the model untouched rather than contributing a
    session's worth of positions built from frames the string never showed.
    """
    async def go():
        server = B.BlinkyServer()
        server.config.pixel_count = PIXELS
        server.config.inter_pixel_delay = 0.0
        server.model.pixel_count = PIXELS
        server.current_session = B.SessionConfig(
            session_id=1, angle_deg=0.0, distance_m=2.0, height_m=1.5,
            device_pitch_deg=15.0)
        server.model.add_session(server.current_session)
        string = FakeString(dead=True)
        server.clients[FakeSensor(server, string)] = "sensor"
        B._make_output = lambda cfg: string
        B.CODED_SETTLE_SEC = 0.0
        B.CODED_FRAME_ATTEMPTS = 2
        await server._run_coded_scan()
        return server

    saved_settle, saved_attempts = B.CODED_SETTLE_SEC, B.CODED_FRAME_ATTEMPTS
    try:
        server = asyncio.run(go())
    finally:
        B.CODED_SETTLE_SEC, B.CODED_FRAME_ATTEMPTS = saved_settle, saved_attempts
    _, dets = server.model.sessions[1]
    assert dets == {}
