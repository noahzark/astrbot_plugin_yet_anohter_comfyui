"""Platform-free ComfyUI engine shared by the bot adapters.

Nothing in this package may import a chat framework (NoneBot, AstrBot,
...). It knows about ComfyUI, workflow files and their ``_reflex.json``
companions, LoRA injection, rate limiting and content audit. Everything
that talks to a user lives in an adapter on top of it.
"""
from .settings import CoreSettings, AuditSettings, TextAuditSettings
from .exceptions import ComfyuiExceptions, ComfyUIPluginError
from . import constants

__version__ = constants.PLUGIN_VERSION

__all__ = [
    "CoreSettings",
    "AuditSettings",
    "TextAuditSettings",
    "ComfyuiExceptions",
    "ComfyUIPluginError",
    "constants",
    "__version__",
]
