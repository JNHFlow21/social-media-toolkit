"""The two external text providers used by the canonical route."""

from .getnote import GETNOTE_INSTALL_HINT, GetNoteTextProvider
from .volcengine import VOLCENGINE_ASR_SECRET_NAME, VolcengineASR

__all__ = [
    "GETNOTE_INSTALL_HINT",
    "GetNoteTextProvider",
    "VOLCENGINE_ASR_SECRET_NAME",
    "VolcengineASR",
]
