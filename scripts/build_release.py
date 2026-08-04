#!/usr/bin/env python3
"""R2-7.3: Build a release package with allowlist and manifest.

Generates a release-manifest.json recording every included file and its
SHA-256 hash. Excludes sensitive, generated, and transient files.

Excluded patterns (mandatory):
    AGENTS_COMPOSED.md          (generated prompt)
    markconfig/profile.md       (personal profile)
    markconfig/secrets.json     (secrets)
    *secret* *credential*       (any secret/credential file)
    _runtime/                   (runtime state)
    _data/private/              (private data)
    cache/ logs/                (transient data)
    .git/ __pycache__/ .pytest_cache/  (VCS and cache)
    vendor/                     (vendored dependencies)
    *.pyc *.pyo                 (compiled Python)

Usage:
    # Dry-run: list what would be included/excluded, no archive created
    python scripts/build_release.py --dry-run

    # Full build: create release archive + manifest
    python scripts/build_release.py --output dist/

    # Specify project root explicitly
    python scripts/build_release.py --root /path/to/project --output dist/
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Mandatory exclusion patterns ──────────────────────────────
# These are ALWAYS excluded, regardless of allowlist.
# Directory names (single-level): any path component matching these is excluded.
MANDATORY_EXCLUDE_DIRS = {
    '_runtime',
    'cache',
    'logs',
    '.git',
    '__pycache__',
    '.pytest_cache',
    'vendor',
    'dist',
    'node_modules',
    '.venv',
    'venv',
}

# Path prefixes: any file whose relative path starts with these is excluded.
MANDATORY_EXCLUDE_PREFIXES = {
    '_data/private',
    '_data/memory',      # personal memory files
    'markconfig/profile.md',
    'markconfig/secrets',
}

# Exact file paths to exclude.
MANDATORY_EXCLUDE_FILES = {
    'AGENTS_COMPOSED.md',
    'markconfig/profile.md',
    'markconfig/secrets.json',
}

# Combined set for backward compatibility with collect_files pruning.
MANDATORY_EXCLUDES = MANDATORY_EXCLUDE_DIRS | {'_data', 'markconfig'}

# Glob patterns for exclusion (matched against basename or relative path)
EXCLUDE_GLOBS = [
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '*secret*',
    '*credential*',
    '*.egg-info',
    '.DS_Store',
    'Thumbs.db',
    '*.log',
    '*.tmp',
    '*.bak',
    '*.swp',
    '*~',
]

# Files that must be present in the release (allowlist verification)
REQUIRED_FILES = [
    'AGENTS.md',
    'AGENTS_BASE.md',
    'opencode.json',
    'requirements.lock.txt',
    'modules/common/__init__.py',
    'modules/common/result.py',
    'modules/common/errors.py',
    'modules/common/time_utils.py',
    'modules/search/contracts.py',
    'modules/search/pipeline.py',
    'modules/scheduler/contracts.py',
    'modules/scheduler/job_store.py',
    'modules/scheduler/daemon.py',
    'modules/prompt/contracts.py',
    'modules/prompt/context.py',
]


def _should_exclude(rel_path: str) -> tuple:
    """Check if a relative path should be excluded from the release.

    Returns:
        tuple: (should_exclude: bool, reason: str)
    """
    parts = Path(rel_path).parts
    rel_posix = Path(rel_path).as_posix()

    # 1. Check exact file excludes
    if rel_posix in MANDATORY_EXCLUDE_FILES:
        return True, f'excluded file: {rel_posix}'

    # 2. Check path prefix excludes (multi-level directories like _data/private)
    for prefix in MANDATORY_EXCLUDE_PREFIXES:
        if rel_posix == prefix or rel_posix.startswith(prefix + '/'):
            return True, f'excluded prefix: {prefix}'

    # 3. Check single-level directory excludes
    for part in parts:
        if part in MANDATORY_EXCLUDE_DIRS:
            return True, f'excluded dir: {part}'

    # 4. Check glob patterns against basename and full relative path
    import fnmatch
    basename = Path(rel_path).name
    for pattern in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(rel_posix, pattern):
            return True, f'matched glob: {pattern}'
        # Also check case-insensitive for secret/credential
        if pattern.startswith('*') and pattern.endswith('*'):
            keyword = pattern[1:-1].lower()
            if keyword in basename.lower() or keyword in rel_posix.lower():
                return True, f'matched keyword: {keyword}'

    return False, ''


def _sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: Path) -> tuple:
    """Collect all files for the release package.

    Returns:
        tuple: (included_files, excluded_files)
            - included_files: list of (rel_path, abs_path) tuples
            - excluded_files: list of (rel_path, reason) tuples
    """
    included = []
    excluded = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip excluded directories in-place (prunes traversal)
        # Use MANDATORY_EXCLUDE_DIRS for single-level pruning,
        # plus '_data' and 'markconfig' for multi-level pruning.
        dirnames[:] = [
            d for d in dirnames
            if d not in MANDATORY_EXCLUDE_DIRS
            and d not in ('_data', 'markconfig')
        ]

        for filename in filenames:
            abs_path = Path(dirpath) / filename
            rel_path = abs_path.relative_to(root)
            rel_str = rel_path.as_posix()

            should_exclude, reason = _should_exclude(rel_str)
            if should_exclude:
                excluded.append((rel_str, reason))
            else:
                included.append((rel_str, abs_path))

    included.sort(key=lambda x: x[0])
    excluded.sort(key=lambda x: x[0])
    return included, excluded


def verify_required_files(included_files: list) -> list:
    """Verify that all required files are present in the release.

    Returns:
        list of missing file paths
    """
    included_set = {rel_path for rel_path, _ in included_files}
    missing = []
    for req in REQUIRED_FILES:
        if req not in included_set:
            missing.append(req)
    return missing


def generate_manifest(included_files: list, root: Path) -> dict:
    """Generate the release manifest with file hashes."""
    manifest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'project_root': str(root),
        'total_files': len(included_files),
        'files': [],
    }

    for rel_path, abs_path in included_files:
        file_hash = _sha256(abs_path)
        stat = abs_path.stat()
        manifest['files'].append({
            'path': rel_path,
            'sha256': file_hash,
            'size_bytes': stat.st_size,
        })

    return manifest


def build_release(root: Path, output_dir: Path, dry_run: bool = False) -> int:
    """Build the release package.

    Args:
        root: Project root directory.
        output_dir: Output directory for the release archive and manifest.
        dry_run: If True, only list what would be included/excluded.

    Returns:
        int: 0 on success, 1 on failure.
    """
    print(f"Project root: {root}")
    print(f"Output directory: {output_dir}")
    print(f"Mode: {'DRY RUN' if dry_run else 'BUILD'}")
    print()

    # Collect files
    included, excluded = collect_files(root)

    # Verify required files
    missing = verify_required_files(included)
    if missing:
        print("ERROR: Required files missing from release:")
        for f in missing:
            print(f"  - {f}")
        return 1

    # Print summary
    print(f"Included files: {len(included)}")
    print(f"Excluded files: {len(excluded)}")
    print()

    # Print excluded files (for audit)
    if excluded:
        print("Excluded files:")
        for rel_path, reason in excluded[:50]:  # Show first 50
            print(f"  - {rel_path}  ({reason})")
        if len(excluded) > 50:
            print(f"  ... and {len(excluded) - 50} more")
        print()

    # Print included files in dry-run
    if dry_run:
        print("Included files (dry run):")
        for rel_path, _ in included[:50]:
            print(f"  + {rel_path}")
        if len(included) > 50:
            print(f"  ... and {len(included) - 50} more")
        print()

    # Verify sensitive files are NOT included
    sensitive_patterns = ['profile.md', 'secrets', 'credential', 'AGENTS_COMPOSED']
    leaked = []
    for rel_path, _ in included:
        for pat in sensitive_patterns:
            if pat in rel_path:
                leaked.append(rel_path)
    if leaked:
        print("ERROR: Sensitive files leaked into release:")
        for f in leaked:
            print(f"  - {f}")
        return 1

    if dry_run:
        print("Dry run complete. No archive created.")
        print(f"Would create: {output_dir}/release-manifest.json")
        return 0

    # Generate manifest
    manifest = generate_manifest(included, root)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest_path = output_dir / 'release-manifest.json'
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Manifest written: {manifest_path}")

    # Create zip archive
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    archive_name = f"agentforge-release-{timestamp}.zip"
    archive_path = output_dir / archive_name

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel_path, abs_path in included:
            zf.write(abs_path, rel_path)
        # Include manifest in archive
        zf.write(manifest_path, 'release-manifest.json')

    print(f"Archive written: {archive_path}")
    print(f"Total files in archive: {len(included)}")
    print(f"Archive size: {archive_path.stat().st_size:,} bytes")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Build a release package with allowlist and manifest.'
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=PROJECT_ROOT,
        help=f'Project root directory (default: {PROJECT_ROOT})',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('dist'),
        help='Output directory for release archive (default: dist)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='List what would be included/excluded without creating archive',
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.exists():
        print(f"ERROR: Project root does not exist: {root}")
        return 1

    output = args.output if args.output.is_absolute() else (root / args.output)

    return build_release(root, output, dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
