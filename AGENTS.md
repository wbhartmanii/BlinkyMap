# BlinkyMap — Agent Context

**Read [`CLAUDE.md`](CLAUDE.md).** It is the single source of truth for this
repository regardless of which agent or tool you are: architecture, how coded
scanning works, the geometry decisions and the reasoning behind them, the test
box, the FPP API, and the testing checklist.

This file used to be a parallel copy for a different assistant, and it rotted.
It described `www/blinkymap/app.js` and a four-tab SPA (the UI is now two
negotiated roles — `control` and `sensor` — and there is no `app.js`), and the
one-pixel-at-a-time detection path with its `asyncio.Queue` sync (superseded by
structured-light scanning, where every frame lights every pixel). An agent
following it would have gone looking for code that no longer exists.

One person maintains this project from several machines. Two context files that
must be kept in step is one more thing to forget, so there is only one.
