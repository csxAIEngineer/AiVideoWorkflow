"""Compatibility shim: canonical module is root-level locks.

Moved to the repo root so AI/skills/registry.py can import pack_limit without
crossing the process-split boundary (AI must not import end). end/* imports
keep working.
"""

import locks as _canonical

globals().update({k: v for k, v in vars(_canonical).items() if not k.startswith("__")})