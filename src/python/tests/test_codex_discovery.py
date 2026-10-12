import os

import pytest

from autoslay.agent import providers


def setup_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(providers.shutil, 'which', lambda _: None)
    monkeypatch.setattr(providers.sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('USERPROFILE', raising=False)
    return tmp_path / 'OpenAI' / 'Codex' / 'bin'


def test_desktop_cli_without_path_selects_latest_install(tmp_path, monkeypatch):
    directory = setup_windows(monkeypatch, tmp_path)
    for version, timestamp in [('old', 100), ('new', 200)]:
        executable = directory / version / 'codex.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'fixture')
        os.utime(executable, (timestamp, timestamp))
    assert providers.resolve_codex_executable('codex') == str(directory / 'new' / 'codex.exe')


def test_path_resolution_takes_precedence(monkeypatch):
    monkeypatch.setattr(providers.shutil, 'which', lambda _: 'configured.exe')
    assert providers.resolve_codex_executable('codex') == 'configured.exe'


def test_redirected_local_appdata_uses_user_install(tmp_path, monkeypatch):
    setup_windows(monkeypatch, tmp_path / 'redirected')
    monkeypatch.setenv('USERPROFILE', str(tmp_path / 'user'))
    executable = tmp_path / 'user' / 'AppData' / 'Local' / 'OpenAI' / 'Codex' / 'bin' / 'v1' / 'codex.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture')
    assert providers.resolve_codex_executable('codex') == str(executable)


@pytest.mark.parametrize('configured', ['missing.exe', 'C:/missing/codex.exe'])
def test_explicit_missing_executable_does_not_fallback(tmp_path, monkeypatch, configured):
    directory = setup_windows(monkeypatch, tmp_path)
    directory.mkdir(parents=True)
    (directory / 'codex.exe').write_bytes(b'fixture')
    assert providers.resolve_codex_executable(configured) is None


def test_no_installation_returns_none(tmp_path, monkeypatch):
    setup_windows(monkeypatch, tmp_path)
    assert providers.resolve_codex_executable('codex') is None


def test_other_platform_does_not_use_windows_install(tmp_path, monkeypatch):
    directory = setup_windows(monkeypatch, tmp_path)
    directory.mkdir(parents=True)
    (directory / 'codex.exe').write_bytes(b'fixture')
    monkeypatch.setattr(providers.sys, 'platform', 'linux')
    assert providers.resolve_codex_executable('codex') is None
