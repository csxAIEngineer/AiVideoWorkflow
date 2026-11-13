"""Production stage loops. Each UI stage is its own worker graph.

``run_p2_asset_loop`` writes asset prompts. ``run_script_loop``,
``run_storyboard_loop`` and ``run_feed_loop`` write the other three stages.
A caller runs one loop and returns. The feed loop's hard gate is 投喂机检.
"""

from Agents.workers import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_BLOCKS,
    STAGE,
    compile_feed_graph,
    compile_p2_asset_graph,
    compile_script_graph,
    compile_storyboard_graph,
    initial_state,
    run_feed_loop,
    run_p2_asset_loop,
    run_script_loop,
    run_storyboard_loop,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MIN_BLOCKS",
    "STAGE",
    "compile_feed_graph",
    "compile_p2_asset_graph",
    "compile_script_graph",
    "compile_storyboard_graph",
    "initial_state",
    "run_feed_loop",
    "run_p2_asset_loop",
    "run_script_loop",
    "run_storyboard_loop",
]
