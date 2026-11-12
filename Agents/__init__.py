"""Production stage loops. This package does not own a pipeline.

`run_p2_asset_loop` is one stage: P2 asset prompts. A later worker calls it
once and returns. It does not write storyboard, feed, or video.
"""

from Agents.p2_asset import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_BLOCKS,
    STAGE,
    compile_p2_asset_graph,
    initial_state,
    run_p2_asset_loop,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MIN_BLOCKS",
    "STAGE",
    "compile_p2_asset_graph",
    "initial_state",
    "run_p2_asset_loop",
]
