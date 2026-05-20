import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_comparison_delta import (
    build_comparison_delta_paths,
    build_comparison_delta_report,
    build_run_directory,
    build_summary_markdown,
    load_json_object,
    main,
    write_report,
)


def _camera_row(camera, *, mean, rms, changed=0.5, status="passed", zoom=14.25):
    return {
        "camera": camera,
        "zoom": zoom,
        "status": status,
        "artifact_status": "metrics_available",
        "metrics": {
            "changed_pixel_ratio": changed,
            "normalized_mean_absolute_channel_delta": mean,
            "normalized_rms_channel_delta": rms,
        },
    }


class MapboxOutdoorsComparisonDeltaTests(unittest.TestCase):
    def test_build_run_directory_and_paths_are_predictable(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/comparison-delta"),
            now=dt.datetime(2026, 5, 20, 18, 50, tzinfo=dt.timezone.utc),
        )
        paths = build_comparison_delta_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/comparison-delta/20260520T185000Z"))
        self.assertEqual(paths.json_path, run_dir / "comparison-delta.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_load_json_object_requires_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            json_path.write_text('{"ok": true}\n', encoding="utf-8")
            self.assertEqual(load_json_object(json_path), {"ok": True})

            json_path.write_text('["not", "object"]\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_object(json_path)

    def test_build_comparison_delta_report_pairs_cameras_and_counts_directions(self):
        baseline = {
            "cameras": [
                _camera_row("chamonix-trails-z14-outdoors", mean=0.04, rms=0.08, changed=0.95),
                _camera_row("zermatt-trails-z18-outdoors", mean=0.03, rms=0.09, changed=0.99),
            ]
        }
        candidate = {
            "cameras": [
                _camera_row("chamonix-trails-z14-outdoors", mean=0.035, rms=0.081, changed=0.94),
                _camera_row("zermatt-trails-z18-outdoors", mean=0.031, rms=0.088, changed=0.99),
            ]
        }

        report = build_comparison_delta_report(
            baseline,
            candidate,
            baseline_label="post-label-cleanups",
            candidate_label="probe",
            now=dt.datetime(2026, 5, 20, 18, 51, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(report["generated_at"], "20260520T185100Z")
        self.assertEqual(report["baseline_label"], "post-label-cleanups")
        self.assertEqual(report["candidate_label"], "probe")
        self.assertEqual(report["camera_count"], 2)
        self.assertEqual(
            report["summary"],
            {
                "mean_improved": 1,
                "mean_worsened": 1,
                "mean_unchanged": 0,
                "mean_unknown": 0,
                "rms_improved": 1,
                "rms_worsened": 1,
                "rms_unchanged": 0,
                "rms_unknown": 0,
            },
        )
        chamonix = report["cameras"][0]
        self.assertEqual(chamonix["camera"], "chamonix-trails-z14-outdoors")
        self.assertAlmostEqual(
            chamonix["metrics"]["normalized_mean_absolute_channel_delta"]["delta"],
            -0.005,
        )
        self.assertEqual(chamonix["mean_delta_direction"], "improved")
        self.assertEqual(chamonix["rms_delta_direction"], "worsened")

    def test_build_comparison_delta_report_preserves_missing_camera_rows(self):
        report = build_comparison_delta_report(
            {"cameras": [_camera_row("baseline-only", mean=0.1, rms=0.2)]},
            {"cameras": [_camera_row("candidate-only", mean=0.3, rms=0.4)]},
        )

        self.assertEqual(
            [row["camera"] for row in report["cameras"]],
            ["baseline-only", "candidate-only"],
        )
        self.assertEqual(report["cameras"][0]["candidate_status"], "missing")
        self.assertIsNone(
            report["cameras"][0]["metrics"]["normalized_mean_absolute_channel_delta"]["delta"]
        )
        self.assertEqual(report["cameras"][1]["baseline_status"], "missing")
        self.assertEqual(report["summary"]["mean_unknown"], 2)
        self.assertEqual(report["summary"]["rms_unknown"], 2)

    def test_build_comparison_delta_report_falls_back_to_baseline_zoom(self):
        baseline_row = _camera_row("camera-a", mean=0.1, rms=0.2, zoom=12.5)
        candidate_row = _camera_row("camera-a", mean=0.08, rms=0.18)
        candidate_row["zoom"] = "unknown"

        report = build_comparison_delta_report(
            {"cameras": [baseline_row]},
            {"cameras": [candidate_row]},
        )

        self.assertEqual(report["cameras"][0]["zoom"], 12.5)
        self.assertIn("| `camera-a` | 12.50 |", build_summary_markdown(report))

    def test_build_summary_markdown_renders_delta_table(self):
        report = build_comparison_delta_report(
            {"cameras": [_camera_row("camera-a", mean=0.05, rms=0.08, changed=0.9, zoom=18)]},
            {"cameras": [_camera_row("camera-a", mean=0.04, rms=0.09, changed=0.91, zoom=18)]},
            baseline_label="baseline",
            candidate_label="candidate",
        )

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors comparison delta", markdown)
        self.assertIn("Baseline: `baseline`", markdown)
        self.assertIn("| `camera-a` | 18.00 | 0.050000000 | 0.040000000 | -0.010000000 |", markdown)
        self.assertIn("| RMS channel delta | 0 | 1 | 0 | 0 |", markdown)

    def test_write_report_outputs_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run"
            paths = build_comparison_delta_paths(run_dir)
            report = build_comparison_delta_report(
                {"cameras": [_camera_row("camera-a", mean=0.05, rms=0.08)]},
                {"cameras": [_camera_row("camera-a", mean=0.04, rms=0.07)]},
            )

            write_report(report, paths)

            written = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(written["camera_count"], 1)
            self.assertIn("camera-a", paths.summary_path.read_text(encoding="utf-8"))

    def test_main_writes_delta_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            baseline = tmp_path / "baseline.json"
            candidate = tmp_path / "candidate.json"
            baseline.write_text(
                json.dumps({"cameras": [_camera_row("camera-a", mean=0.05, rms=0.08)]}),
                encoding="utf-8",
            )
            candidate.write_text(
                json.dumps({"cameras": [_camera_row("camera-a", mean=0.04, rms=0.07)]}),
                encoding="utf-8",
            )

            stdout = io.StringIO()

            class SequencedDateTime(dt.datetime):
                calls = 0

                @classmethod
                def now(cls, tz=None):
                    cls.calls += 1
                    seconds = 0 if cls.calls == 1 else 1
                    timestamp = dt.datetime(
                        2026,
                        5,
                        20,
                        18,
                        51,
                        seconds,
                        tzinfo=dt.timezone.utc,
                    )
                    return timestamp if tz is None else timestamp.astimezone(tz)

            with patch(
                "qfit.validation.mapbox_outdoors_comparison_delta.dt.datetime",
                SequencedDateTime,
            ):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            str(baseline),
                            str(candidate),
                            "--baseline-label",
                            "baseline",
                            "--candidate-label",
                            "candidate",
                            "--output-root",
                            str(tmp_path / "delta"),
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(SequencedDateTime.calls, 1)
            self.assertIn("Delta report:", stdout.getvalue())
            output_dir = tmp_path / "delta" / "20260520T185100Z"
            self.assertTrue((output_dir / "summary.md").exists())
            written = json.loads((output_dir / "comparison-delta.json").read_text(encoding="utf-8"))
            self.assertEqual(written["generated_at"], "20260520T185100Z")


if __name__ == "__main__":
    unittest.main()
