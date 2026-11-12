"""RadPi server."""

import os

# Serve files with aiohttp's own chunked writer instead of asyncio's loop.sendfile. Over TLS,
# asyncio falls back to a copy loop of its own, and on Python 3.13 that loop crashes with
# "AttributeError: 'NoneType' object has no attribute '_set_app_protocol'" whenever a browser
# hangs up part-way through a file (an audio player seeking in a clip does this all the time).
# aiohttp's writer handles the hang-up quietly. aiohttp reads this when it is first imported,
# so it has to be set here, before anything imports it.
os.environ.setdefault("AIOHTTP_NOSENDFILE", "1")
