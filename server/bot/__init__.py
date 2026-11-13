"""RadPi's AI agent: listens to the channel, learns voices, and talks back. See agent.py.

Only imported when bot.enabled is true in config.yaml. Its heavier packages (Whisper, the speaker
model, Piper) are in requirements-bot.txt and are only needed for the backends you pick.
"""

from .agent import Agent, BOT_CLIENT_ID  # noqa: F401
