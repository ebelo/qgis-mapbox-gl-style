from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "iterations" / "index.json"
DEFAULT_DEBUG_ROOT = REPO_ROOT / "debug" / "iterations"
TOKEN_ENV_NAMES = (
    "QGIS_MAPBOX_GL_STYLE_MAPBOX_TOKEN",
    "MAPBOX_ACCESS_TOKEN",
    "QFIT_MAPBOX_ACCESS_TOKEN",
)


class IterationError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_index(path: Path = INDEX_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IterationError(f"Iteration index not found: {path}") from exc


def write_index(index: dict[str, Any], path: Path = INDEX_PATH) -> None:
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def iteration_rows(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows = index.get("iterations", [])
    if not isinstance(rows, list):
        raise IterationError("Iteration index has no list-valued 'iterations' field.")
    return rows


def find_iteration(index: dict[str, Any], iteration_id: str) -> dict[str, Any]:
    for iteration in iteration_rows(index):
        if str(iteration.get("id")) == iteration_id:
            return iteration
    raise IterationError(f"Unknown iteration: {iteration_id}")


def print_iterations(index: dict[str, Any]) -> None:
    rows = iteration_rows(index)
    print("ID   Commit   Date        Label")
    print("---  -------  ----------  -----")
    for row in rows:
        commit = str(row.get("filtered_commit") or row.get("source_commit") or "")
        date = str(row.get("created_at") or "")[:10]
        print(f"{row.get('id', ''):<3}  {commit[:7]:<7}  {date:<10}  {row.get('label', '')}")


def resolve_camera_arguments(index: dict[str, Any], camera_set: str) -> list[str]:
    camera_sets = index.get("camera_sets", {})
    if not isinstance(camera_sets, dict):
        raise IterationError("Iteration index has no object-valued 'camera_sets' field.")
    camera_config = camera_sets.get(camera_set)
    if not isinstance(camera_config, dict):
        known = ", ".join(sorted(str(name) for name in camera_sets))
        raise IterationError(f"Unknown camera set '{camera_set}'. Known camera sets: {known}")
    render_arguments = camera_config.get("render_arguments", [])
    if not isinstance(render_arguments, list) or not all(isinstance(item, str) for item in render_arguments):
        raise IterationError(f"Camera set '{camera_set}' has invalid render_arguments.")
    return list(render_arguments)


def resolve_token(explicit_token: str | None) -> str:
    if explicit_token:
        return explicit_token.strip()
    for name in TOKEN_ENV_NAMES:
        token = os.environ.get(name, "").strip()
        if token:
            return token
    return ""


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def render_iteration(args: argparse.Namespace) -> int:
    index = load_index()
    iteration = find_iteration(index, args.iteration)
    camera_arguments = resolve_camera_arguments(index, args.camera_set)
    commit = str(iteration.get("filtered_commit") or iteration.get("source_commit") or "").strip()
    if not commit:
        raise IterationError(f"Iteration {args.iteration} has no commit to render.")

    token = resolve_token(args.mapbox_token)
    if not token:
        names = ", ".join(TOKEN_ENV_NAMES)
        raise IterationError(f"Mapbox token required via --mapbox-token or one of: {names}")

    timestamp = utc_timestamp()
    output_root = Path(args.output_root).resolve() if args.output_root else DEFAULT_DEBUG_ROOT
    run_dir = output_root / str(iteration["id"]) / timestamp
    worktree_dir = run_dir / "worktree"
    comparison_dir = run_dir / "comparison"
    render_args = [*camera_arguments, "--output-root", str(comparison_dir)]
    if args.skip_browser:
        render_args.append("--skip-browser")
    if args.skip_qgis:
        render_args.append("--skip-qgis")
    if args.skip_diff:
        render_args.append("--skip-diff")

    worktree_command = ["git", "worktree", "add", "--detach", str(worktree_dir), commit]
    comparison_script = worktree_dir / "validation" / "mapbox_outdoors_comparison.py"
    render_command = [args.python_executable, str(comparison_script), *render_args]

    run_manifest = {
        "iteration": iteration,
        "camera_set": args.camera_set,
        "created_at": timestamp,
        "worktree": _relative(worktree_dir),
        "comparison_output": _relative(comparison_dir),
        "worktree_command": worktree_command,
        "render_command": render_command,
        "token_source": "environment" if not args.mapbox_token else "argument-redacted",
    }

    if args.dry_run:
        print(json.dumps(run_manifest, indent=2))
        return 0

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8")
    subprocess.run(worktree_command, cwd=REPO_ROOT, check=True)

    env = os.environ.copy()
    env["MAPBOX_ACCESS_TOKEN"] = token
    completed = subprocess.run(render_command, cwd=worktree_dir, env=env, check=False)
    run_manifest["return_code"] = completed.returncode
    (run_dir / "run.json").write_text(json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8")
    return int(completed.returncode)


def current_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def next_iteration_id(rows: list[dict[str, Any]]) -> str:
    numeric_ids = [int(str(row.get("id"))) for row in rows if str(row.get("id", "")).isdigit()]
    return f"{(max(numeric_ids) if numeric_ids else 0) + 1:03d}"


def accept_iteration(args: argparse.Namespace) -> int:
    index = load_index()
    rows = iteration_rows(index)
    head = current_head()
    run_path = Path(args.from_run)
    try:
        run_reference = run_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        run_reference = str(run_path)

    rows.append(
        {
            "id": next_iteration_id(rows),
            "label": args.label,
            "source_repo": "ebelo/qgis-mapbox-gl-style",
            "source_commit": head,
            "filtered_commit": head,
            "source_prs": [],
            "source_issue": None,
            "style_owner": index.get("style", {}).get("owner", "mapbox"),
            "style_id": index.get("style", {}).get("id", "outdoors-v12"),
            "camera_set": args.camera_set or index.get("default_camera_set", "issue-949"),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source_run": run_reference,
            "notes": args.notes or "Accepted from a local render/improvement run.",
        }
    )
    write_index(index)
    print(f"Accepted iteration {rows[-1]['id']}: {args.label}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage QGIS Mapbox GL Style iterations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List known style iterations.")

    render = subparsers.add_parser("render", help="Render a historical style iteration.")
    render.add_argument("--iteration", required=True, help="Iteration id from iterations/index.json.")
    render.add_argument("--camera-set", default=None, help="Camera set from the iteration index.")
    render.add_argument("--output-root", help="Output root. Defaults to debug/iterations.")
    render.add_argument("--python", dest="python_executable", default=sys.executable, help="Python executable to run the harness.")
    render.add_argument("--mapbox-token", help="Mapbox token. It is passed via environment, not written to manifests.")
    render.add_argument("--dry-run", action="store_true", help="Print the worktree/render commands without executing them.")
    render.add_argument("--skip-browser", action="store_true", help="Forward --skip-browser to the comparison harness.")
    render.add_argument("--skip-qgis", action="store_true", help="Forward --skip-qgis to the comparison harness.")
    render.add_argument("--skip-diff", action="store_true", help="Forward --skip-diff to the comparison harness.")

    accept = subparsers.add_parser("accept", help="Append a future run as the next iteration.")
    accept.add_argument("--from-run", required=True, help="Run directory that produced the accepted visual change.")
    accept.add_argument("--label", required=True, help="Human-readable iteration label.")
    accept.add_argument("--camera-set", help="Camera set used by the accepted run.")
    accept.add_argument("--notes", help="Notes to store in the iteration manifest.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print_iterations(load_index())
            return 0
        if args.command == "render":
            index = load_index()
            if args.camera_set is None:
                args.camera_set = find_iteration(index, args.iteration).get(
                    "camera_set",
                    index.get("default_camera_set", "issue-949"),
                )
            return render_iteration(args)
        if args.command == "accept":
            return accept_iteration(args)
    except IterationError as exc:
        parser.exit(2, f"error: {exc}\n")
    parser.exit(2, "error: unknown command\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
