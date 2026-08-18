# How xLights detects pixels, and what we should take from it

Source: `src-ui-wx/model/GenerateCustomModelDialog.cpp` in
xLightsSequencer/xLights (read 2026-08-18).

## What they do

They do **not** light one pixel at a time. Every frame lights *every* pixel at
once, and a pixel's colour in frame *i* is the *i*-th base-3 digit of its index
— `0` red, `1` green, `2` blue. Locating pixel N means intersecting (pixel-wise
`Min`) the matching colour-channel image across all frames; only the true pixel
satisfies every constraint.

Frame count is `GetBits(n)` = base-3 digits of n, **plus two check digits**
(`check = 2 - (digitsum % 3)`, then `(check + 1) % 3`), so a misread is
detectable rather than silently wrong.

Per-candidate cleanup is `ProcessB`: threshold, then N rounds of erode followed
by N rounds of dilate (despeckle), then `FindPixelA` — the centroid of *all*
white pixels remaining.

## Why it matters for us

| | ours (1 pixel/frame) | xLights |
|---|---|---|
| 24 pixels | 24 frames, ~12 s | 5 frames, ~2.5 s |
| 500 pixels | 500 frames, ~250 s | 8 frames, ~4 s |
| 5000 pixels | 5000 frames | 10 frames |

Beyond speed, it attacks failures we actually hit:

- **Reflections** must match the pixel's colour in every frame to survive the
  intersection: 1 in 243 at 24 pixels, 1 in 6561 at 500. Our single-frame
  detector has no equivalent defence, and reflections repeatedly won.
- **Occlusion becomes honest for free.** A hidden pixel leaves nothing
  surviving the intersection, so "not visible" falls out of the geometry
  instead of a tuned confidence threshold.
- **Auto-exposure stops hunting.** Every frame lights the same number of LEDs,
  so scene brightness is constant. We alternate a dark baseline against a
  single lit pixel — the exact swing that makes a phone camera re-expose.
- **Handheld drift stops mattering** at 2.5 s per scan instead of 12 s.

## The obstacle to settle first

This needs **per-pixel colours simultaneously**. `FPPOutput` currently drives
`Test Start` with a channel range and one colour, which cannot express it.
Options, in rough order of preference:

1. E1.31 — `E131Output` already exists and sends arbitrary per-channel values,
   but the controller must be in bridge mode. The current test box (.111) runs
   as a player.
2. An FPP API path that sets individual channels directly.
3. Uploading a short generated sequence and playing it.

Nothing else about the scheme is difficult; this is the piece that decides the
shape of the implementation.

## Worth noting

Their pipeline assumes a *recorded video* that is then processed, using flag
frames to synchronise. We drive a live camera over a WebSocket, so we would
capture on demand after each frame is lit — simpler, and it removes their sync
problem entirely.
