#!/usr/bin/env python3
"""Capability Registry CLI.

Validation consumes a lossless DiscoveryReport. A malformed or missing manifest
therefore cannot disappear from the valid-module list and accidentally produce
a zero exit status.
"""
from __future__ import annotations

import argparse
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _cmd_list(args: argparse.Namespace) -> int:
    from modules.registry.discovery import discover_modules_report
    from modules.registry.schema import manifest_to_dict

    report = discover_modules_report(strict=False)
    if args.json:
        print(
            json.dumps(
                {
                    "total": len(report.manifests),
                    "modules": [manifest_to_dict(item) for item in report.manifests],
                    "discovery": report.to_dict(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        if report.manifests:
            print(f"{'Name':<20} {'Version':<12} {'Experimental':<14} Capabilities")
            print("-" * 80)
            for manifest in report.manifests:
                capabilities = ", ".join(
                    capability.name for capability in manifest.capabilities
                ) or "-"
                print(
                    f"{manifest.name:<20} {manifest.version:<12} "
                    f"{('yes' if manifest.experimental else 'no'):<14} {capabilities}"
                )
            print(f"\nTotal: {len(report.manifests)} modules")
        else:
            print("No valid modules found.")
        for issue in report.errors:
            print(f"ERROR {issue.module}: {issue.message}", file=sys.stderr)
        for issue in report.warnings:
            print(f"WARN  {issue.module}: {issue.message}", file=sys.stderr)
    return 1 if report.errors else 0


def _cmd_health(args: argparse.Namespace) -> int:
    from modules.registry.discovery import discover_modules_report
    from modules.registry.health import check_credentials, run_health_checks

    report = discover_modules_report(strict=False)
    if report.errors:
        if args.json:
            print(json.dumps({"discovery": report.to_dict()}, indent=2, ensure_ascii=False))
        else:
            for issue in report.errors:
                print(f"ERROR {issue.module}: {issue.message}", file=sys.stderr)
        return 1
    if not report.manifests:
        print("No modules found.")
        return 1

    health_results = run_health_checks(modules=report.manifests)
    if args.json:
        output = {
            "total_modules": len(report.manifests),
            "modules": [],
            "summary": {
                status: sum(
                    1 for result in health_results.values() if result.overall == status
                )
                for status in ("healthy", "degraded", "unhealthy", "unknown")
            },
        }
        for manifest in report.manifests:
            health = health_results.get(manifest.name)
            output["modules"].append(
                {
                    "name": manifest.name,
                    "health": health.to_dict() if health else None,
                    "credentials": check_credentials(manifest),
                }
            )
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"{'Module':<20} {'Status':<12} {'Checks':<12} Credentials")
        print("-" * 70)
        for manifest in report.manifests:
            health = health_results.get(manifest.name)
            status = health.overall if health else "unknown"
            checks = f"{health.passed_count}/{len(health.checks)}" if health else "-"
            credentials = check_credentials(manifest)
            present = sum(1 for credential in credentials if credential["present"])
            print(
                f"{manifest.name:<20} {status:<12} {checks:<12} "
                f"{present}/{len(credentials)}"
            )

    return 1 if any(
        result.overall == "unhealthy" for result in health_results.values()
    ) else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from modules.registry.discovery import discover_modules_report

    report = discover_modules_report(strict=args.strict)
    if args.json:
        output = report.to_dict()
        output["strict"] = args.strict
        output["modules"] = [
            {"name": item.name, "version": item.version}
            for item in report.manifests
        ]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        mode = "strict" if args.strict else "compatible"
        print(f"Registry validation mode: {mode}")
        print(f"Scanned modules: {len(report.scanned_modules)}")
        print(f"Valid manifests: {len(report.manifests)}")
        for manifest in report.manifests:
            print(f"  OK  {manifest.name} v{manifest.version}")
        if report.warnings:
            print(f"\nWarnings: {len(report.warnings)}")
            for issue in report.warnings:
                print(f"  WARN  {issue.module} [{issue.code}] {issue.message}")
        if report.errors:
            print(f"\nErrors: {len(report.errors)}")
            for issue in report.errors:
                print(f"  ERROR {issue.module} [{issue.code}] {issue.message}")

    return 0 if report.valid else 1


def _cmd_missing(args: argparse.Namespace) -> int:
    from modules.registry.discovery import discover_modules_report

    report = discover_modules_report(strict=False)
    missing = [
        issue.module for issue in report.errors if issue.code == "manifest_missing"
    ]
    if not missing:
        print("All code modules have manifest.json files.")
        return 0
    print("Modules missing manifest.json:")
    for module in missing:
        print(f"  - {module}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modules.registry",
        description="Capability Registry — list, health, and validate manifests.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List registered modules")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=_cmd_list)

    health_parser = sub.add_parser("health", help="Run module health checks")
    health_parser.add_argument("--json", action="store_true")
    health_parser.set_defaults(func=_cmd_health)

    validate_parser = sub.add_parser("validate", help="Validate all manifests")
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help="Require canonical fields and validate names, entrypoints, storage, and capabilities",
    )
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=_cmd_validate)

    missing_parser = sub.add_parser("missing", help="List missing manifests")
    missing_parser.set_defaults(func=_cmd_missing)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
