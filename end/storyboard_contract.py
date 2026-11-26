"""Compatibility shim: canonical module is root-level storyboard_contract.

Moved to the repo root so AI/skills/registry.py can import it without crossing
the process-split boundary (AI must not import end). end/* imports keep working.
Note: the canonical module still lazily imports end.seam_ledger / end.render_loop
inside two functions; those call sites run after all modules are loaded.
"""

import storyboard_contract as _canonical

globals().update({k: v for k, v in vars(_canonical).items() if not k.startswith("__")})