import subprocess
import sys

import pytest

from nymetro_eventlocator.cli import COMMANDS, build_parser, main


def test_every_command_module_is_complete_and_unique():
    names = [c.NAME for c in COMMANDS]
    assert len(names) == len(set(names))
    for c in COMMANDS:
        assert callable(c.add_parser) and callable(c.run) and isinstance(c.NEEDS_CONFIG, bool), c.__name__
    assert {c.NAME for c in COMMANDS if not c.NEEDS_CONFIG} == {"demo", "init", "secrets"}  # work before a config exists


@pytest.mark.parametrize("name", [c.NAME for c in COMMANDS])
def test_every_command_has_help(name, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([name, "--help"])
    assert exc.value.code == 0 and name in capsys.readouterr().out


def test_config_errors_are_reported_not_raised(tmp_path, capsys):
    assert main(["-c", str(tmp_path / "missing.yaml"), "check-config"]) == 2
    assert "config error" in capsys.readouterr().err


def test_version_matches_package_metadata(capsys):
    from importlib.metadata import version

    from nymetro_eventlocator import __version__
    from nymetro_eventlocator.config import HttpSettings
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"nymetro_eventlocator {__version__}"
    assert __version__ == version("nymetro_eventlocator")   # pyproject.toml and __init__.py agree
    assert f"/{__version__} " in HttpSettings().user_agent


def test_python_dash_m_entry_point():
    out = subprocess.run([sys.executable, "-m", "nymetro_eventlocator", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "check-config" in out.stdout
