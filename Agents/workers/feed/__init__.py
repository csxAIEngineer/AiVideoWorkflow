"""Feed-packing worker: generate → critique → 投喂机检.

Import this package as ``Agents.workers.feed``. The implementation is split
by concern (style lock, extract, packing plan, shot skeleton, targeted repair,
debug log, and the graph). The names re-exported here are the public API.

``check_feed`` / ``validate_prompt`` (H3, by segment) is the hard gate. The
duration rule is a hard gate on top of that script: merged shots may share a
unit, the total may not shrink below the storyboard, dialogue may not be
dropped, and a span over 15s is split instead of clamped.

Shot headings and cut times are known from the storyboard. A prose blob with
no ``[Shot N]`` is not hung under ``[Shot 1]``. An empty skeleton body, a
``subject_definitions`` that is only the style lock, and an empty
``overall_soundscape`` fail the feed gate. A repair folds only the units the
machine report names onto the last complete draft.
"""

from Agents.workers.feed.common import (
    STAGE,
    TASK,
    GENERATOR_SYSTEM,
    H3_SECTIONS,
    H3_OMITTED,
    H3_FILL_ONLY,
    CRITIC_SYSTEM,
    FeedState,
    shot_duration_list,
    packing_groups,
)
from Agents.workers.feed.style import (
    STYLE_LOCK_LABEL,
    script_style_line,
    agreed_style,
    style_lock_line,
    style_lock_rule,
    style_lock_failures,
    inject_style_lock,
)
from Agents.workers.feed.extract import (
    extract_feed,
    restore_h3_scaffold,
)
from Agents.workers.feed.plan import (
    h3_packing_scaffold,
    packing_plan_note,
    correct_unit_durations,
    restore_spoken_lines,
    spoken_lines_for_units,
    place_owned_spoken_lines,
    packing_repair_targets,
    packing_unit_asset_lines,
    inject_unit_assets,
    apply_packing_plan,
)
from Agents.workers.feed.skeleton import (
    format_cut_timecode,
    packing_shot_skeleton,
    format_shot_skeleton,
    shot_skeleton_failures,
    inject_shot_skeleton,
)
from Agents.workers.feed.repair import (
    named_repair_units,
    repair_unit_collapsed,
    draft_is_complete,
    last_complete_draft,
    merge_targeted_repair,
)
from Agents.workers.feed.debug import (
    resolve_feed_debug_dir,
    packing_debug_snapshot,
    write_feed_debug,
)
from Agents.workers.feed.loop import (
    generator_messages,
    critic_messages,
    compile_feed_graph,
    run_feed_loop,
)
from Agents.workers.critic import critic_user
from Agents.workers.generator import run_generator

__all__ = [
    "STAGE",
    "TASK",
    "GENERATOR_SYSTEM",
    "H3_SECTIONS",
    "H3_OMITTED",
    "H3_FILL_ONLY",
    "CRITIC_SYSTEM",
    "FeedState",
    "shot_duration_list",
    "packing_groups",
    "STYLE_LOCK_LABEL",
    "script_style_line",
    "agreed_style",
    "style_lock_line",
    "style_lock_rule",
    "style_lock_failures",
    "inject_style_lock",
    "extract_feed",
    "restore_h3_scaffold",
    "h3_packing_scaffold",
    "packing_plan_note",
    "correct_unit_durations",
    "restore_spoken_lines",
    "spoken_lines_for_units",
    "place_owned_spoken_lines",
    "packing_repair_targets",
    "packing_unit_asset_lines",
    "inject_unit_assets",
    "apply_packing_plan",
    "format_cut_timecode",
    "packing_shot_skeleton",
    "format_shot_skeleton",
    "shot_skeleton_failures",
    "inject_shot_skeleton",
    "named_repair_units",
    "repair_unit_collapsed",
    "draft_is_complete",
    "last_complete_draft",
    "merge_targeted_repair",
    "resolve_feed_debug_dir",
    "packing_debug_snapshot",
    "write_feed_debug",
    "generator_messages",
    "critic_messages",
    "compile_feed_graph",
    "run_feed_loop",
    "critic_user",
    "run_generator",
]
