"""Count shots from ffmpeg scene scores.

Soft cuts between similar shots can score just above 0.25. A camera move
inside one shot can also spike just under that. Peaks at or above 0.22 are
candidates. Inside a 0.40s window only the stronger peak is kept, so a
dissolve tail or a bump right before a hard cut does not become its own shot.
"""

import re

from media import MediaError, inspect_av, write_scene_metadata

MIN_SCORE = 0.22
MERGE_GAP = 0.40
_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)")


def detect_shots(video_path, work_dir):
    info = inspect_av(video_path)
    if not info["has_video"]:
        raise MediaError("这个文件里没有画面。")
    metadata = write_scene_metadata(video_path, work_dir)
    peaks = parse_scene_metadata(metadata)
    cuts = choose_cuts(peaks, info["duration"])
    shots = build_shots(cuts, info["duration"])
    return {
        "duration": round(info["duration"], 3),
        "has_audio": info["has_audio"],
        "shot_count": len(shots),
        "shots": shots,
    }


def parse_scene_metadata(text):
    pending = None
    peaks = []
    for line in (text or "").splitlines():
        time_match = _TIME_RE.search(line)
        score_match = _SCORE_RE.search(line)
        if time_match:
            pending = float(time_match.group(1))
        if score_match is not None and pending is not None:
            peaks.append((pending, float(score_match.group(1))))
            pending = None
    return peaks


def choose_cuts(peaks, duration, min_score=MIN_SCORE, merge_gap=MERGE_GAP):
    """Return (time, score) cut points, one per shot boundary."""
    eligible = [
        (time_s, score)
        for time_s, score in peaks
        if score >= min_score and 0.05 < time_s < float(duration) - 0.05
    ]
    ordered = sorted(eligible, key=lambda item: item[0])
    return _suppress_nearby(ordered, merge_gap)


def build_shots(cuts, duration):
    bounds = [0.0] + [time_s for time_s, _score in cuts] + [float(duration)]
    shots = []
    for index in range(len(bounds) - 1):
        start = bounds[index]
        end = bounds[index + 1]
        if end - start < 0.04:
            continue
        shots.append(
            {
                "index": len(shots) + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "start_label": format_clock(start),
                "end_label": format_clock(end),
            }
        )
    if not shots:
        shots.append(
            {
                "index": 1,
                "start": 0.0,
                "end": round(float(duration), 3),
                "start_label": format_clock(0),
                "end_label": format_clock(duration),
            }
        )
    return shots


def format_clock(seconds):
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    remain = total - minutes * 60
    return "%d:%05.2f" % (minutes, remain)


def _suppress_nearby(ordered, merge_gap):
    kept = []
    for time_s, score in ordered:
        if kept and time_s - kept[-1][0] < merge_gap:
            if score > kept[-1][1]:
                kept = kept[:-1] + [(time_s, score)]
            continue
        kept = kept + [(time_s, score)]
    return kept
