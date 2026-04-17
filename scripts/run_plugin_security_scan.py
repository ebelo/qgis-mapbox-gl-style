#!/usr/bin/env python3
"""Build the packaged plugin and run local security/quality scans on it."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import package_plugin

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_DIR = ROOT / "debug" / "plugin-security-scan"


@dataclass(frozen=True)
class ScanResult:
    name: str
    command: tuple[str, ...]
    report_path: Path
    stderr_path: Path
    exit_code: int
    has_findings: bool

    @property
    def status(self) -> str:
        if self.has_findings:
            return "findings"
        if self.exit_code == 0:
            return "clean"
        return f"error ({self.exit_code})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build qfit's plugin ZIP, extract it, and run Bandit, detect-secrets, "
            "and Flake8 against the packaged contents."
        )
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help=f"Directory where local scan reports are written (default: {DEFAULT_REPORTS_DIR})",
    )
    parser.add_argument(
        "--allow-findings",
        action="store_true",
        help="Return success even when scanners report findings, while still failing on execution errors.",
    )
    return parser.parse_args(argv)


def prepare_scan_tree(reports_dir: Path) -> tuple[Path, Path]:
    if reports_dir.exists():
        shutil.rmtree(reports_dir)
    archive_path = package_plugin.build_zip()
    extracted_dir = reports_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted_dir)

    plugin_root = find_extracted_plugin_root(extracted_dir)
    return archive_path, plugin_root


def find_extracted_plugin_root(extracted_dir: Path) -> Path:
    roots = sorted(path for path in extracted_dir.iterdir() if path.is_dir())
    if len(roots) != 1:
        raise RuntimeError(
            f"Expected exactly one plugin directory in {extracted_dir}, found {len(roots)}"
        )
    return roots[0]


def run_scan(name: str, command: list[str], report_path: Path, *, findings_checker=None) -> ScanResult:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = report_path.with_suffix(report_path.suffix + ".stderr.txt")
    try:
        completed = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        report_path.write_text("", encoding="utf-8")
        stderr_path.write_text(str(exc), encoding="utf-8")
        return ScanResult(
            name=name,
            command=tuple(command),
            report_path=report_path,
            stderr_path=stderr_path,
            exit_code=127,
            has_findings=False,
        )

    report_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    has_findings = False
    if findings_checker is not None:
        has_findings = findings_checker(completed.returncode, completed.stdout)
    elif completed.returncode != 0:
        has_findings = True

    return ScanResult(
        name=name,
        command=tuple(command),
        report_path=report_path,
        stderr_path=stderr_path,
        exit_code=completed.returncode,
        has_findings=has_findings,
    )


def exit_code_one_is_findings(exit_code: int, _stdout: str) -> bool:
    return exit_code == 1


def detect_secrets_has_findings(exit_code: int, stdout: str) -> bool:
    if exit_code != 0:
        return False
    payload = json.loads(stdout or "{}")
    results = payload.get("results", {})
    return any(entries for entries in results.values())


def evaluate_results(results: list[ScanResult], *, allow_findings: bool) -> int:
    execution_errors = [result for result in results if result.exit_code != 0 and not result.has_findings]
    if execution_errors:
        return 2
    if any(result.has_findings for result in results) and not allow_findings:
        return 1
    return 0


def write_summary(
    summary_path: Path,
    *,
    archive_path: Path,
    plugin_root: Path,
    results: list[ScanResult],
) -> None:
    lines = [
        f"Archive: {archive_path}",
        f"Packaged plugin root: {plugin_root}",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"[{result.name}] {result.status}",
                f"  command: {' '.join(result.command)}",
                f"  report: {result.report_path}",
                f"  stderr: {result.stderr_path}",
                "",
            ]
        )
    summary_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_scan_commands(plugin_root: Path, reports_dir: Path) -> list[tuple[str, list[str], Path, object | None]]:
    return [
        (
            "bandit",
            [sys.executable, "-m", "bandit", "-r", str(plugin_root), "-f", "json", "-q"],
            reports_dir / "bandit.json",
            exit_code_one_is_findings,
        ),
        (
            "detect-secrets",
            [
                sys.executable,
                "-m",
                "detect_secrets.main",
                "scan",
                "--all-files",
                str(plugin_root),
            ],
            reports_dir / "detect-secrets.json",
            detect_secrets_has_findings,
        ),
        (
            "flake8",
            [
                sys.executable,
                "-m",
                "flake8",
                "--extend-exclude=vendor/",
                str(plugin_root),
            ],
            reports_dir / "flake8.txt",
            exit_code_one_is_findings,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    archive_path, plugin_root = prepare_scan_tree(args.reports_dir)

    results = [
        run_scan(name, command, report_path, findings_checker=findings_checker)
        for name, command, report_path, findings_checker in build_scan_commands(
            plugin_root, args.reports_dir
        )
    ]

    summary_path = args.reports_dir / "summary.txt"
    write_summary(summary_path, archive_path=archive_path, plugin_root=plugin_root, results=results)

    print(summary_path.read_text(encoding="utf-8"), end="")
    exit_code = evaluate_results(results, allow_findings=args.allow_findings)
    if exit_code == 1:
        print("Scan findings detected. See local reports above.", file=sys.stderr)
    elif exit_code == 2:
        print("One or more scan commands failed to run cleanly.", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
