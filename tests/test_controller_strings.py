"""Reading the pixel count from the controller instead of asking for it twice.

Run: python3 -m pytest tests/test_controller_strings.py

The numbers below are the documented test box: two configured strings, 24
pixels from channel 1 and 100 from channel 1001, with channels 73-1000 mapping
to nothing. A scan configured for 100 pixels on that box lights 24 and then
appears to stall, which is the exact confusion this removes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import blinkymap_server as B  # noqa: E402


# FPP's own co-pixelStrings.json shape: the channel output carries a 1-based
# startChannel, and each virtual string a 0-BASED one. That mismatch is the only
# subtle part of reading this file.
FPP_PIXEL_STRINGS = {
    "channelOutputs": [{
        "type": "RPIWS281X", "enabled": 1,
        "startChannel": 1, "channelCount": 1500,
        "outputs": [
            {"portNumber": 0, "virtualStrings": [
                {"description": "Test String 1", "startChannel": 0,
                 "pixelCount": 24, "colorOrder": "RGB"}]},
            {"portNumber": 1, "virtualStrings": [
                {"description": "Test String 2", "startChannel": 1000,
                 "pixelCount": 100, "colorOrder": "RGB"}]},
            {"portNumber": 2, "virtualStrings": [
                {"description": "", "startChannel": 0, "pixelCount": 0}]},
        ],
    }],
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return json.loads(json.dumps(self._payload))


def fake_get(payloads):
    """Serve a payload per endpoint; anything else 404s, as FPP does."""
    def get(url, timeout=None):
        for name, payload in payloads.items():
            if url.endswith(name):
                return FakeResponse(payload)
        return FakeResponse({}, status=404)
    return get


def with_controller(payloads, fn):
    saved = B.requests.get
    B.requests.get = fake_get(payloads)
    try:
        return fn()
    finally:
        B.requests.get = saved


def test_reads_both_strings_with_their_real_channel_ranges():
    result = with_controller({"co-pixelStrings": FPP_PIXEL_STRINGS},
                             lambda: B._controller_strings("192.168.25.111"))
    assert result["ok"]
    assert result["source"] == "co-pixelStrings"
    assert [(s["pixel_count"], s["start_channel"], s["end_channel"])
            for s in result["strings"]] == [(24, 1, 72), (100, 1001, 1300)]


def test_unpopulated_ports_are_not_strings():
    """A port configured with no pixels is not a string to scan."""
    result = with_controller({"co-pixelStrings": FPP_PIXEL_STRINGS},
                             lambda: B._controller_strings("host"))
    assert all(s["pixel_count"] > 0 for s in result["strings"])
    assert len(result["strings"]) == 2


def test_zero_based_start_channels_are_recognised():
    """A string reading 0 proves the file is 0-based: channel 0 does not exist.

    Without that inference every channel would be reported one low, and a scan
    driven one channel off lights the wrong pixels — visibly, but only after the
    operator has walked to their first position.
    """
    result = with_controller({"co-pixelStrings": FPP_PIXEL_STRINGS},
                             lambda: B._controller_strings("host"))
    assert result["zero_based"] is True
    assert result["strings"][0]["start_channel"] == 1


def test_start_channels_are_passed_through_when_undecidable():
    """With nothing reading 0 the file cannot be read either way on its own.

    Guessing would be worse than being literal: the UI shows the range so the
    operator can check it against FPP's own output page.
    """
    payload = {"channelOutputs": [{"startChannel": 1, "outputs": [
        {"portNumber": 0, "virtualStrings": [
            {"description": "Arch", "startChannel": 50, "pixelCount": 10}]}]}]}
    result = with_controller({"co-pixelStrings": payload},
                             lambda: B._controller_strings("host"))
    assert result["zero_based"] is False
    assert result["strings"][0]["start_channel"] == 50


def test_bbb_capes_are_read_from_their_own_endpoint():
    payload = {"channelOutputs": [{"type": "BBB48String", "outputs": [
        {"portNumber": 0, "virtualStrings": [
            {"description": "Tree", "startChannel": 0, "pixelCount": 50}]}]}]}
    result = with_controller({"co-bbbStrings": payload},
                             lambda: B._controller_strings("host"))
    assert result["ok"] and result["source"] == "co-bbbStrings"
    assert result["strings"][0]["pixel_count"] == 50


def test_unreachable_controller_reports_rather_than_raises():
    def boom(url, timeout=None):
        raise OSError("No route to host")
    saved = B.requests.get
    B.requests.get = boom
    try:
        result = B._controller_strings("10.0.0.1")
    finally:
        B.requests.get = saved
    assert result["ok"] is False
    assert result["strings"] == []
    assert "No route to host" in result["error"]


# ── Which string a start channel means ────────────────────────────────────────

def strings():
    return with_controller({"co-pixelStrings": FPP_PIXEL_STRINGS},
                           lambda: B._controller_strings("host"))["strings"]


def test_start_channel_selects_its_own_string():
    assert B._pick_string(strings(), 1)["pixel_count"] == 24
    assert B._pick_string(strings(), 1001)["pixel_count"] == 100


def test_a_channel_inside_a_string_selects_that_string():
    """Starting part-way along a string still identifies the string.

    The count is then an overstatement, but naming the wrong port would be worse
    and the operator can see both in the list.
    """
    assert B._pick_string(strings(), 1100)["start_channel"] == 1001


def test_a_channel_belonging_to_nothing_falls_back_to_the_first_string():
    # Channels 73-1000 map to nothing on the test box.
    assert B._pick_string(strings(), 500)["start_channel"] == 1
    assert B._pick_string([], 1) is None
