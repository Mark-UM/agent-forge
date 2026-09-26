"""Release source boundaries for a clean Git checkout."""
import json
import subprocess
import zipfile

from scripts import build_release as release
from scripts.build_release import collect_files


def test_collect_files_uses_git_index_and_keeps_safe_examples(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    tracked = {
        'AGENTS.md': '# AgentForge\n',
        'modules/bootstrap/secrets_env.py': '# Reviewed source code\n',
        'markconfig/profile.example.md': '# Example profile\n',
        '_data/memory/MEMORY.example.md': '# Example memory\n',
        'markconfig/secrets.example.json': '{"API_KEY": "placeholder"}\n',
        'markconfig/profile.md': '# Private profile\n',
    }
    for relative, content in tracked.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(tmp_path), 'add', '--', *tracked], check=True,
    )
    external = tmp_path / '.opencode' / 'skills' / 'upstream' / 'SKILL.md'
    external.parent.mkdir(parents=True)
    external.write_text('# Upstream skill\n', encoding='utf-8')

    included, excluded = collect_files(tmp_path)

    assert {relative for relative, _ in included} == {
        'AGENTS.md',
        'modules/bootstrap/secrets_env.py',
        'markconfig/profile.example.md',
        'markconfig/secrets.example.json',
        '_data/memory/MEMORY.example.md',
    }
    assert ('markconfig/profile.md', 'excluded file: markconfig/profile.md') in excluded


def test_build_uses_committed_blob_when_worktree_contains_secret(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'AGENTS.md').write_text('# AgentForge\n', encoding='utf-8')
    example = tmp_path / 'markconfig' / 'secrets.example.json'
    example.parent.mkdir()
    example.write_text('{"API_KEY": "placeholder"}\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(tmp_path), 'add', '--', 'AGENTS.md',
         'markconfig/secrets.example.json'], check=True,
    )
    subprocess.run(
        ['git', '-C', str(tmp_path), '-c', 'user.name=Test',
         '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline'],
        check=True,
    )
    example.write_text('{"API_KEY": "REAL_SECRET"}\n', encoding='utf-8')
    monkeypatch.setattr(release, 'REQUIRED_FILES', [
        'AGENTS.md', 'markconfig/secrets.example.json',
    ])

    output = tmp_path / 'dist'
    assert release.build_release(tmp_path, output) == 0
    archive = next(output.glob('*.zip'))
    with zipfile.ZipFile(archive) as zipped:
        packaged = zipped.read('markconfig/secrets.example.json')
    assert b'placeholder' in packaged
    assert b'REAL_SECRET' not in packaged
    manifest = json.loads((output / 'release-manifest.json').read_text(encoding='utf-8'))
    assert manifest['files'][1]['sha256'] == release._sha256(packaged)

    subprocess.run([
        'git', '-C', str(tmp_path), 'add', '--', 'markconfig/secrets.example.json',
    ], check=True)
    staged_output = tmp_path / 'staged-dist'
    assert release.build_release(tmp_path, staged_output) == 1
    assert not staged_output.exists()
