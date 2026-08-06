"""Public compatibility surface for the Agent Forge 2.0 Slice 1 kernel.

The implementation remains private until a later slice adds an executable
Kernel lifecycle and a canonical Capability Manifest.
"""
from modules._kernel import *  # noqa: F401,F403
from modules._kernel import __all__
