import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shots import build_shots, choose_cuts, format_clock, parse_scene_metadata

DURATION = 47.37
PEAKS = [
    (1.7, 0.257249),
    (1.83333, 0.239281),
    (4.53333, 0.607962),
    (5.0, 0.183329),
    (6.0, 0.236862),
    (6.33333, 0.636175),
    (11.7, 0.465037),
    (22.1, 0.692320),
    (26.1333, 0.948222),
    (30.4667, 1.0),
    (36.6, 0.773707),
    (39.5, 0.548935),
    (41.3333, 0.523258),
    (43.9, 0.559155),
    (46.1667, 0.525764),
]
METADATA = """
frame:0    pts:26112   pts_time:1.7
lavfi.scene_score=0.257249
frame:4    pts:92160   pts_time:6
lavfi.scene_score=0.236862
frame:5    pts:97280   pts_time:6.33333
lavfi.scene_score=0.636175
""".strip()


class ShotBoundaryTest(unittest.TestCase):
    def test_soft_cut_is_kept_and_nearby_bump_is_not(self):
        cuts = choose_cuts(PEAKS, DURATION)
        times = [item[0] for item in cuts]

        self.assertEqual(len(times), 12)
        self.assertAlmostEqual(times[0], 1.7, places=2)
        self.assertAlmostEqual(times[1], 4.53333, places=2)
        self.assertAlmostEqual(times[2], 6.33333, places=2)
        self.assertNotIn(6.0, times)
        self.assertNotIn(1.83333, times)
        self.assertNotIn(5.0, times)

    def test_shot_count_includes_the_opening_shot(self):
        shots = build_shots(choose_cuts(PEAKS, DURATION), DURATION)

        self.assertEqual(len(shots), 13)
        self.assertEqual(shots[0]["start"], 0.0)
        self.assertAlmostEqual(shots[0]["end"], 1.7, places=2)
        self.assertAlmostEqual(shots[-1]["start"], 46.1667, places=2)
        self.assertEqual(shots[-1]["end"], DURATION)
        self.assertEqual(shots[0]["start_label"], "0:00.00")
        self.assertEqual(shots[0]["end_label"], "0:01.70")
        self.assertEqual(shots[0]["index"], 1)

    def test_no_peaks_is_one_shot(self):
        shots = build_shots(choose_cuts([], 12.0), 12.0)

        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["end"], 12.0)

    def test_metadata_parser_pairs_time_and_score(self):
        peaks = parse_scene_metadata(METADATA)

        self.assertEqual(peaks[0], (1.7, 0.257249))
        self.assertEqual(peaks[-1][0], 6.33333)

    def test_clock_format(self):
        self.assertEqual(format_clock(0), "0:00.00")
        self.assertEqual(format_clock(65.2), "1:05.20")


class RealVideoShotTest(unittest.TestCase):
    def test_sample_video_has_thirteen_shots(self):
        from media import find_ffmpeg
        from shots import detect_shots

        video = ROOT / "test.mp4"
        if find_ffmpeg() is None or not video.is_file():
            self.skipTest("需要本机 ffmpeg 和 test.mp4")
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            detected = detect_shots(video, folder)

        self.assertEqual(detected["shot_count"], 13)
        self.assertGreater(detected["duration"], 47)
        self.assertTrue(detected["has_audio"])
        self.assertAlmostEqual(detected["shots"][0]["end"], 1.7, places=1)
        self.assertAlmostEqual(detected["shots"][2]["start"], 4.533, places=1)
