#!/usr/bin/env python3
"""R2-B.6: Registry CLI — list, health, and validate commands.

Usage:
    python -m modules.registry list [--json]
    python -m modules.registry health [--json]
    python -m modules.registry validate
    python -m modules.registry missing
"""
import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _cmd_list(args: argparse.Namespace) -> int:
    """List all registered modules."""
    from modules.registry.discovery import discover_modules
    from modules.registry.schema import manifest_to_dict

    manifests = discover_modules()

    if args.json:
        output = {
            'total': len(manifests),
            'modules': [manifest_to_dict(m) for m in manifests],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        if not manifests:
            print("No modules found.")
            return 0
        print(f"{'Name':<20} {'Version':<12} {'Experimental':<14} {'Capabilities'}")
        print("-" * 80)
        for m in manifests:
            exp = "yes" if m.experimental else "no"
            caps = ", ".join(c.name for c in m.capabilities) or "-"
            print(f"{m.name:<20} {m.version:<12} {exp:<14} {caps}")
        print(f"\nTotal: {len(manifests)} modules")

    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    """Run health checks for all modules."""
    from modules.registry.discovery import discover_modules
    from modules.registry.health import run_health_checks, check_credentials

    manifests = discover_modules()
    if not manifests:
        print("No modules found.")
        return 1

    health_results = run_health_checks(manifests=manifests)

    if args.json:
        output = {
            'total_modules': len(manifests),
            'modules': [],
            'summary': {
                'healthy': sum(1 for h in health_results.values() if h.overall == 'healthy'),
                'degraded': sum(1 for h in health_results.values() if h.overall == 'degraded'),
                'unhealthy': sum(1 for h in health_results.values() if h.overall == 'unhealthy'),
                'unknown': sum(1 for h in health_results.values() if h.overall == 'unknown'),
            },
        }
        for m in manifests:
            health = health_results.get(m.name)
            creds = check_credentials(m)
            output['modules'].append({
                'name': m.name,
                'health': health.to_dict() if health else None,
                'credentials': creds,
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"{'Module':<20} {'Status':<12} {'Checks':<12} {'Credentials'}")
        print("-" * 70)
        for m in manifests:
            health = health_results.get(m.name)
            if health:
                status = health.overall
                checks = f"{health.passed_count}/{len(health.checks)}"
            else:
                status = "unknown"
                checks = "-"
            creds = check_credentials(m)
            cred_summary = f"{sum(1 for c in creds if c['present'])}/{len(creds)}"
            print(f"{m.name:<20} {status:<12} {checks:<12} {cred_summary}")

        # Summary
        summary = {
            'healthy': sum(1 for h in health_results.values() if h.overall == 'healthy'),
            'degraded': sum(1 for h in health_results.values() if h.overall == 'degraded'),
            'unhealthy': sum(1 for h in health_results.values() if h.overall == 'unhealthy'),
            'unknown': sum(1 for h in health_results.values() if h.overall == 'unknown'),
        }
        print(f"\nSummary: {summary['healthy']} healthy, {summary['degraded']} degraded, "
              f"{summary['unhealthy']} unhealthy, {summary['unknown']} unknown")

    # Exit non-zero if any module is unhealthy
    has_unhealthy = any(h.overall == 'unhealthy' for h in health_results.values())
    return 1 if has_unhealthy else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate all manifest.json files."""
    from modules.registry.discovery import discover_modules, find_missing_manifests

    manifests = discover_modules()
    missing = find_missing_manifests()

    if not manifests and not missing:
        print("No modules found.")
        return 0

    print(f"Valid manifests: {len(manifests)}")
    for m in manifests:
        print(f"  OK  {m.name} v{m.version}")

    if missing:
        print(f"\nMissing manifests: {len(missing)}")
        for name in missing:
            print(f"  MISSING  {name}")
        return 1

    return 0


def _cmd_missing(args: argparse.Namespace) -> int:
    """List modules that lack manifest.json."""
    from modules.registry.discovery import find_missing_manifests

    missing = find_missing_manifests()
    if not missing:
        print("All modules have manifests.")
        return 0

    print("Modules missing manifest.json:")
    for name in missing:
        print(f"  - {name}")
    return 0


def main(argv: list[str] = None) -> int:
    parser = argparse.ArgumentParser(
        prog='modules.registry',
        description='Capability Registry — list, health, and validate module manifests.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_list = sub.add_parser('list', help='List all registered modules')
    p_list.add_argument('--json', action='store_true', help='Output as JSON')
    p_list.set_defaults(func=_cmd_list)

    p_health = sub.add_parser('health', help='Run health checks for all modules')
    p_health.add_argument('--json', action='store_true', help='Output as JSON')
    p_health.set_defaults(func=_cmd_health)

    p_validate = sub.add_parser('validate', help='Validate all manifest.json files')
    p_validate.set_defaults(func=_cmd_validate)

    p_missing = sub.add_parser('missing', help='List modules without manifest.json')
    p_missing.set_defaults(func=_cmd_missing)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
