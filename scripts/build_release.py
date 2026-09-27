#!/usr/bin/env python3
"""Build a release package from Git-indexed files with a safety allowlist.

Generates a release-manifest.json recording every included file and its
SHA-256 hash. Excludes sensitive, generated, and transient files.

Excluded patterns (mandatory):
    AGENTS_COMPOSED.md          (generated prompt)
    markconfig/profile.md       (personal profile)
    markconfig/secrets.json     (secrets)
    markconfig/secrets*        (private configuration, except example)
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
import subprocess
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
SAFE_EXAMPLE_FILES = {
    'markconfig/profile.example.md',
    'markconfig/secrets.example.json',
    '_data/memory/MEMORY.example.md',
    '_data/memory/README-INIT.md',
}
PRIVATE_TREE_ALLOWLIST = SAFE_EXAMPLE_FILES | {
    'markconfig/README.md',
    'markconfig/authority_whitelist.json',
}

# Glob patterns for exclusion (matched against basename or relative path)
EXCLUDE_GLOBS = [
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '*.key',
    '*.pem',
    '*.pfx',
    '.env',
    '.env.*',
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
    '.opencode/package-lock.json',
    'markconfig/profile.example.md',
    'markconfig/secrets.example.json',
    '_data/memory/MEMORY.example.md',
    '_data/memory/README-INIT.md',
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

    # Private trees ship only named public templates and configuration.
    if (rel_posix.startswith(('markconfig/', '_data/')) and
            rel_posix not in PRIVATE_TREE_ALLOWLIST):
        return True, f'excluded private tree: {rel_posix}'
    if rel_posix in SAFE_EXAMPLE_FILES:
        return False, ''

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


    return False, ''


def _sha256(content: bytes) -> str:
    """Compute SHA-256 for the exact Git blob that will be packaged."""
    return hashlib.sha256(content).hexdigest()


def collect_files(root: Path) -> tuple:
    """Collect safe paths and bytes from the Git index, not the worktree."""
    root = root.resolve(strict=True)
    git_root = subprocess.run(
        ['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
        capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout.strip()
    if Path(git_root).resolve(strict=True) != root:
        raise ValueError(f'Project root must be the Git worktree root: {root}')
    output = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '--stage', '-z'],
        capture_output=True, check=True,
    ).stdout

    included = []
    excluded = []
    for record in output.split(b'\0'):
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, blob_id, stage = metadata.decode('ascii').split()
        rel_str = raw_path.decode('utf-8')
        rel_path = Path(rel_str)
        if rel_path.is_absolute() or '..' in rel_path.parts:
            raise ValueError(f'Unsafe Git-index path: {rel_str}')
        if stage != '0':
            raise ValueError(f'Unmerged Git-index path: {rel_str}')
        should_exclude, reason = _should_exclude(rel_str)
        if should_exclude:
            excluded.append((rel_str, reason))
            continue
        if mode not in {'100644', '100755'}:
            excluded.append((rel_str, f'non-regular Git mode: {mode}'))
            continue
        content = subprocess.run(
            ['git', '-C', str(root), 'cat-file', 'blob', blob_id],
            capture_output=True, check=True,
        ).stdout
        included.append((rel_str, content))

    included.sort(key=lambda item: item[0])
    excluded.sort(key=lambda item: item[0])
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


def generate_manifest(included_files: list, root: Path,
                      excluded_files: list | None = None) -> dict:
    """Generate the release manifest with file hashes."""
    manifest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'git-index',
        'source_commit': subprocess.run(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, encoding='utf-8', check=True,
        ).stdout.strip(),
        'total_files': len(included_files),
        'excluded_files': [
            {'path': path, 'reason': reason}
            for path, reason in (excluded_files or [])
        ],
        'files': [],
    }

    for rel_path, content in included_files:
        file_hash = _sha256(content)
        manifest['files'].append({
            'path': rel_path,
            'sha256': file_hash,
            'size_bytes': len(content),
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
    leaked = [
        rel_path for rel_path, _ in included
        if rel_path in MANDATORY_EXCLUDE_FILES or
        (rel_path.startswith('markconfig/secrets') and
         rel_path not in SAFE_EXAMPLE_FILES)
    ]
    if leaked:
        print("ERROR: Sensitive files leaked into release:")
        for f in leaked:
            print(f"  - {f}")
        return 1

    if dry_run:
        print("Dry run complete. No archive created.")
        print(f"Would create: {output_dir}/release-manifest.json")
        return 0

    # Publish only a committed index. Worktree edits never enter the package.
    staged = subprocess.run(
        ['git', '-C', str(root), 'diff', '--cached', '--quiet', 'HEAD', '--'],
        check=False,
    )
    if staged.returncode != 0:
        print('ERROR: Commit staged changes before building a release.')
        return 1

    # Generate manifest
    manifest = generate_manifest(included, root, excluded)

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
        for rel_path, content in included:
            zf.writestr(rel_path, content)
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
