from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

try:
    from .mapbox_outdoors_path_pedestrian_focus import (
        build_run_directory as _build_timestamped_run_directory,
        load_json_object,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from mapbox_outdoors_path_pedestrian_focus import (  # type: ignore[no-redef]
        build_run_directory as _build_timestamped_run_directory,
        load_json_object,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-comparison-delta"
DELTA_METRIC_KEYS = (
    "changed_pixel_ratio",
    "normalized_mean_absolute_channel_delta",
    "normalized_rms_channel_delta",
)
DIRECTION_BUCKETS = ("improved", "worsened", "unchanged", "unknown")
TOP_MOVEMENT_LIMIT = 5


class ComparisonDeltaPaths(NamedTuple):
    run_dir: Path
    json_path: Path
    summary_path: Path


def _report_timestamp(now: dt.datetime | None = None) -> str:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    return timestamp.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_run_directory(
    *,
    output_root: Path | None = None,
    now: dt.datetime | None = None,
) -> Path:
    return _build_timestamped_run_directory(
        output_root=output_root if output_root is not None else DEFAULT_OUTPUT_ROOT,
        now=now,
    )


def build_comparison_delta_paths(run_dir: Path) -> ComparisonDeltaPaths:
    return ComparisonDeltaPaths(
        run_dir=run_dir,
        json_path=run_dir / "comparison-delta.json",
        summary_path=run_dir / "summary.md",
    )


def _camera_rows(summary: Mapping[str, object]) -> list[Mapping[str, object]]:
    cameras = summary.get("cameras")
    if not isinstance(cameras, list):
        return []
    return [camera for camera in cameras if isinstance(camera, Mapping)]


def _camera_name(row: Mapping[str, object]) -> str | None:
    camera = row.get("camera")
    return camera if isinstance(camera, str) and camera else None


def _metric_value(row: Mapping[str, object] | None, key: str) -> float | None:
    if row is None:
        return None
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _row_status(row: Mapping[str, object] | None) -> str:
    if row is None:
        return "missing"
    status = row.get("status")
    return status if isinstance(status, str) and status else "unknown"


def _row_artifact_status(row: Mapping[str, object] | None) -> str:
    if row is None:
        return "missing"
    status = row.get("artifact_status")
    return status if isinstance(status, str) and status else "unknown"


def _row_zoom(row: Mapping[str, object] | None) -> float | None:
    if row is None:
        return None
    zoom = row.get("zoom")
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
        return None
    return float(zoom)


def _ordered_camera_names(
    baseline_rows: list[Mapping[str, object]],
    candidate_rows: list[Mapping[str, object]],
) -> list[str]:
    names: list[str] = []
    seen = set()
    for row in (*baseline_rows, *candidate_rows):
        camera = _camera_name(row)
        if camera is not None and camera not in seen:
            names.append(camera)
            seen.add(camera)
    return names


def _metric_delta(
    baseline_row: Mapping[str, object] | None,
    candidate_row: Mapping[str, object] | None,
    key: str,
) -> dict[str, float | None]:
    baseline = _metric_value(baseline_row, key)
    candidate = _metric_value(candidate_row, key)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "delta": None if baseline is None or candidate is None else candidate - baseline,
    }


def _delta_direction(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0:
        return "improved"
    if value > 0:
        return "worsened"
    return "unchanged"


def _direction_counts(prefix: str, directions: list[str]) -> dict[str, int]:
    return {f"{prefix}_{bucket}": directions.count(bucket) for bucket in DIRECTION_BUCKETS}


def _metric_delta_value(row: Mapping[str, object], key: str, field: str) -> object:
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    metric = metrics.get(key)
    if not isinstance(metric, Mapping):
        return None
    return metric.get(field)


def _numeric_delta(row: Mapping[str, object], key: str) -> float | None:
    value = _metric_delta_value(row, key, "delta")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _largest_metric_movement_rows(
    rows: list[Mapping[str, object]],
    *,
    limit: int = TOP_MOVEMENT_LIMIT,
) -> list[dict[str, object]]:
    scored_rows: list[tuple[float, str, dict[str, object]]] = []
    for row in rows:
        mean_delta = _numeric_delta(row, "normalized_mean_absolute_channel_delta")
        rms_delta = _numeric_delta(row, "normalized_rms_channel_delta")
        deltas = [abs(delta) for delta in (mean_delta, rms_delta) if delta is not None]
        if not deltas:
            continue
        score = max(deltas)
        if score == 0:
            continue
        camera = row.get("camera")
        movement = {
            "camera": camera if isinstance(camera, str) else "-",
            "zoom": row.get("zoom"),
            "mean_delta": mean_delta,
            "rms_delta": rms_delta,
            "changed_pixel_ratio_delta": _numeric_delta(row, "changed_pixel_ratio"),
            "mean_delta_direction": row.get("mean_delta_direction"),
            "rms_delta_direction": row.get("rms_delta_direction"),
        }
        scored_rows.append((score, str(movement["camera"]), movement))
    scored_rows.sort(key=lambda item: (-item[0], item[1]))
    return [row for _score, _camera, row in scored_rows[:limit]]


def _report_path(path: Path, *, artifact_base_dir: Path | None) -> str:
    if artifact_base_dir is not None:
        try:
            return Path(os.path.relpath(path.resolve(), artifact_base_dir.resolve())).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _input_artifact_row(
    summary: Mapping[str, object],
    summary_path: Path | None,
    *,
    artifact_base_dir: Path | None,
) -> dict[str, str]:
    row: dict[str, str] = {}
    if summary_path is not None:
        row["summary_json"] = _report_path(summary_path, artifact_base_dir=artifact_base_dir)
        row["summary_markdown"] = _report_path(
            summary_path.with_name("summary.md"),
            artifact_base_dir=artifact_base_dir,
        )

    contact_sheet = summary.get("contact_sheet")
    if isinstance(contact_sheet, str) and contact_sheet:
        contact_sheet_path = Path(contact_sheet)
        if not contact_sheet_path.is_absolute() and summary_path is not None:
            contact_sheet_path = summary_path.parent / contact_sheet_path
        row["contact_sheet"] = _report_path(
            contact_sheet_path,
            artifact_base_dir=artifact_base_dir,
        )
    return row


def build_comparison_delta_report(
    baseline_summary: Mapping[str, object],
    candidate_summary: Mapping[str, object],
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
    baseline_summary_path: Path | None = None,
    candidate_summary_path: Path | None = None,
    artifact_base_dir: Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    baseline_rows = _camera_rows(baseline_summary)
    candidate_rows = _camera_rows(candidate_summary)
    baseline_by_camera = {
        camera: row for row in baseline_rows if (camera := _camera_name(row)) is not None
    }
    candidate_by_camera = {
        camera: row for row in candidate_rows if (camera := _camera_name(row)) is not None
    }

    rows = []
    for camera in _ordered_camera_names(baseline_rows, candidate_rows):
        baseline_row = baseline_by_camera.get(camera)
        candidate_row = candidate_by_camera.get(camera)
        candidate_zoom = _row_zoom(candidate_row)
        metric_deltas = {
            key: _metric_delta(baseline_row, candidate_row, key)
            for key in DELTA_METRIC_KEYS
        }
        rows.append(
            {
                "camera": camera,
                "zoom": (
                    candidate_zoom
                    if candidate_zoom is not None
                    else _row_zoom(baseline_row)
                ),
                "baseline_status": _row_status(baseline_row),
                "candidate_status": _row_status(candidate_row),
                "baseline_artifact_status": _row_artifact_status(baseline_row),
                "candidate_artifact_status": _row_artifact_status(candidate_row),
                "metrics": metric_deltas,
                "mean_delta_direction": _delta_direction(
                    metric_deltas["normalized_mean_absolute_channel_delta"]["delta"]
                ),
                "rms_delta_direction": _delta_direction(
                    metric_deltas["normalized_rms_channel_delta"]["delta"]
                ),
            }
        )

    mean_directions = [
        row["mean_delta_direction"]
        for row in rows
        if isinstance(row.get("mean_delta_direction"), str)
    ]
    rms_directions = [
        row["rms_delta_direction"]
        for row in rows
        if isinstance(row.get("rms_delta_direction"), str)
    ]
    report: dict[str, object] = {
        "generated_at": _report_timestamp(now),
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "camera_count": len(rows),
        "summary": _direction_counts("mean", mean_directions)
        | _direction_counts("rms", rms_directions),
        "cameras": rows,
    }
    largest_metric_movements = _largest_metric_movement_rows(rows)
    if largest_metric_movements:
        report["largest_metric_movements"] = largest_metric_movements
    input_artifacts = {
        label: artifacts
        for label, artifacts in {
            "baseline": _input_artifact_row(
                baseline_summary,
                baseline_summary_path,
                artifact_base_dir=artifact_base_dir,
            ),
            "candidate": _input_artifact_row(
                candidate_summary,
                candidate_summary_path,
                artifact_base_dir=artifact_base_dir,
            ),
        }.items()
        if artifacts
    }
    if input_artifacts:
        report["input_artifacts"] = input_artifacts
    return report


def _format_float(value: object, *, precision: int = 9) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.{precision}f}"


def _format_delta(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):+.9f}"


def _artifact_path_cell(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    return f"`{value}`" if isinstance(value, str) and value else "-"


def _append_input_artifacts_section(lines: list[str], report: Mapping[str, object]) -> None:
    input_artifacts = report.get("input_artifacts")
    if not isinstance(input_artifacts, Mapping):
        return
    baseline = input_artifacts.get("baseline")
    candidate = input_artifacts.get("candidate")
    rows = (
        ("Baseline", baseline if isinstance(baseline, Mapping) else {}),
        ("Candidate", candidate if isinstance(candidate, Mapping) else {}),
    )
    lines.extend(
        [
            "## Inputs",
            "",
            "| Input | Summary JSON | Summary Markdown | Contact sheet |",
            "| --- | --- | --- | --- |",
        ]
    )
    for label, row in rows:
        lines.append(
            "| "
            f"{label} | "
            f"{_artifact_path_cell(row, 'summary_json')} | "
            f"{_artifact_path_cell(row, 'summary_markdown')} | "
            f"{_artifact_path_cell(row, 'contact_sheet')} |"
        )
    lines.append("")


def _append_largest_metric_movements_section(lines: list[str], report: Mapping[str, object]) -> None:
    movements = report.get("largest_metric_movements")
    if not isinstance(movements, list) or not movements:
        return
    lines.extend(
        [
            "## Largest Metric Movements",
            "",
            (
                "| Camera | z | Mean delta | RMS delta | Changed ratio delta | "
                "Direction (mean/RMS) |"
            ),
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for movement in movements:
        if not isinstance(movement, Mapping):
            continue
        direction = (
            f"{movement.get('mean_delta_direction') or '-'}/"
            f"{movement.get('rms_delta_direction') or '-'}"
        )
        lines.append(
            "| "
            f"`{movement.get('camera', '-')}` | "
            f"{_format_float(movement.get('zoom'), precision=2)} | "
            f"{_format_delta(movement.get('mean_delta'))} | "
            f"{_format_delta(movement.get('rms_delta'))} | "
            f"{_format_delta(movement.get('changed_pixel_ratio_delta'))} | "
            f"{direction} |"
        )
    lines.append("")


def build_summary_markdown(report: Mapping[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Mapbox Outdoors comparison delta",
        "",
        f"Generated: `{report.get('generated_at', '-')}`",
        "",
        f"Baseline: `{report.get('baseline_label', 'baseline')}`",
        f"Candidate: `{report.get('candidate_label', 'candidate')}`",
        "",
    ]
    _append_input_artifacts_section(lines, report)
    _append_largest_metric_movements_section(lines, report)
    lines.extend(
        [
            "## Summary",
            "",
            "| Metric | Improved | Worsened | Unchanged | Unknown |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                "| Mean absolute channel delta | "
                f"{summary.get('mean_improved', 0)} | "
                f"{summary.get('mean_worsened', 0)} | "
                f"{summary.get('mean_unchanged', 0)} | "
                f"{summary.get('mean_unknown', 0)} |"
            ),
            (
                "| RMS channel delta | "
                f"{summary.get('rms_improved', 0)} | "
                f"{summary.get('rms_worsened', 0)} | "
                f"{summary.get('rms_unchanged', 0)} | "
                f"{summary.get('rms_unknown', 0)} |"
            ),
            "",
            "## Cameras",
            "",
            (
                "| Camera | z | Mean baseline | Mean candidate | Mean delta | "
                "RMS baseline | RMS candidate | RMS delta | Changed ratio delta | Status |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    cameras = report.get("cameras")
    rows = cameras if isinstance(cameras, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = f"{row.get('baseline_status', '-')}/{row.get('candidate_status', '-')}"
        mean_baseline = _metric_delta_value(
            row,
            "normalized_mean_absolute_channel_delta",
            "baseline",
        )
        mean_candidate = _metric_delta_value(
            row,
            "normalized_mean_absolute_channel_delta",
            "candidate",
        )
        mean_delta = _metric_delta_value(
            row,
            "normalized_mean_absolute_channel_delta",
            "delta",
        )
        rms_baseline = _metric_delta_value(
            row,
            "normalized_rms_channel_delta",
            "baseline",
        )
        rms_candidate = _metric_delta_value(
            row,
            "normalized_rms_channel_delta",
            "candidate",
        )
        rms_delta = _metric_delta_value(row, "normalized_rms_channel_delta", "delta")
        changed_ratio_delta = _metric_delta_value(row, "changed_pixel_ratio", "delta")
        lines.append(
            "| "
            f"`{row.get('camera', '-')}` | "
            f"{_format_float(row.get('zoom'), precision=2)} | "
            f"{_format_float(mean_baseline)} | "
            f"{_format_float(mean_candidate)} | "
            f"{_format_delta(mean_delta)} | "
            f"{_format_float(rms_baseline)} | "
            f"{_format_float(rms_candidate)} | "
            f"{_format_delta(rms_delta)} | "
            f"{_format_delta(changed_ratio_delta)} | "
            f"{status} |"
        )
    lines.extend(
        [
            "",
            "Notes:",
            (
                "- Negative mean/RMS deltas indicate the candidate moved closer "
                "to the Mapbox GL reference."
            ),
            (
                "- This is a manual validation aid; visual contact sheets still "
                "decide whether a style probe is worth promoting."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, object], paths: ComparisonDeltaPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two Mapbox Outdoors all-camera summary.json reports.",
    )
    parser.add_argument("baseline_summary", type=Path)
    parser.add_argument("candidate_summary", type=Path)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_summary = load_json_object(args.baseline_summary)
    candidate_summary = load_json_object(args.candidate_summary)
    now = dt.datetime.now(dt.timezone.utc)
    paths = build_comparison_delta_paths(
        build_run_directory(output_root=args.output_root, now=now)
    )
    report = build_comparison_delta_report(
        baseline_summary,
        candidate_summary,
        baseline_label=args.baseline_label,
        candidate_label=args.candidate_label,
        baseline_summary_path=args.baseline_summary,
        candidate_summary_path=args.candidate_summary,
        artifact_base_dir=paths.run_dir,
        now=now,
    )
    write_report(report, paths)
    print(f"Delta JSON: {paths.json_path}")
    print(f"Delta report: {paths.summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
