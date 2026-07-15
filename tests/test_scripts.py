from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_executable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)
    target.chmod(0o755)


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_client_script_keeps_environment_and_config_in_project(tmp_path):
    project = tmp_path / "project"
    client_script = project / "scripts" / "client.sh"
    _copy_executable(PROJECT_ROOT / "scripts" / "client.sh", client_script)
    venv_bin = project / ".client-venv" / "bin"
    _write_executable(venv_bin / "python", "#!/bin/sh\nexit 0\n")
    invocation_log = tmp_path / "client-invocation.log"
    _write_executable(
        venv_bin / "feishu-ioctl",
        (
            "#!/bin/sh\n"
            'printf "%s\\n" "$FEISHU_IO_CONFIG" >"$CLIENT_INVOCATION_LOG"\n'
            'printf "%s\\n" "$XDG_CACHE_HOME" >>"$CLIENT_INVOCATION_LOG"\n'
            'printf "%s\\n" "$PIP_CACHE_DIR" >>"$CLIENT_INVOCATION_LOG"\n'
            'printf "%s\\n" "$*" >>"$CLIENT_INVOCATION_LOG"\n'
        ),
    )
    environment = os.environ.copy()
    environment["CLIENT_INVOCATION_LOG"] = str(invocation_log)
    environment["FEISHU_IO_CONFIG"] = str(tmp_path / "outside.json")

    result = subprocess.run(
        [str(client_script), "send", "test", "hello"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        str(project / ".client" / "client.json"),
        str(project / ".client" / "cache"),
        str(project / ".client" / "cache" / "pip"),
        "send test hello",
    ]
    assert stat.S_IMODE((project / ".client").stat().st_mode) == 0o700
    assert stat.S_IMODE((project / ".client" / "cache").stat().st_mode) == 0o700


def test_client_script_rejects_external_config_path(tmp_path):
    project = tmp_path / "project"
    client_script = project / "scripts" / "client.sh"
    _copy_executable(PROJECT_ROOT / "scripts" / "client.sh", client_script)

    result = subprocess.run(
        [str(client_script), "--config", str(tmp_path / "outside.json"), "config"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "config is managed inside the repository" in result.stderr
    assert not (project / ".client").exists()


def test_server_launcher_explains_missing_env_file(tmp_path):
    launcher = tmp_path / "project" / "scripts" / "run-server.sh"
    _copy_executable(PROJECT_ROOT / "scripts" / "run-server.sh", launcher)

    result = subprocess.run(
        [str(launcher)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "copy .env.example" in result.stderr


def test_systemd_installer_writes_and_enables_user_service(tmp_path):
    project = tmp_path / "project"
    installer = project / "scripts" / "install-server-service.sh"
    _copy_executable(
        PROJECT_ROOT / "scripts" / "install-server-service.sh",
        installer,
    )
    _write_executable(project / "scripts" / "run-server.sh", "#!/bin/sh\nexit 0\n")
    env_file = project / ".env"
    env_file.write_text("FEISHU_IO_API_KEY=test\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    systemctl_log = tmp_path / "systemctl.log"
    _write_executable(
        fake_bin / "systemctl",
        '#!/bin/sh\nprintf "%s\\n" "$*" >>"$SYSTEMCTL_LOG"\n',
    )
    config_home = tmp_path / "config"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(config_home),
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "SYSTEMCTL_LOG": str(systemctl_log),
            "USER": "test-user",
        }
    )

    result = subprocess.run(
        [str(installer)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    unit_file = config_home / "systemd" / "user" / "feishu-io.service"
    unit = unit_file.read_text(encoding="utf-8")
    assert result.returncode == 0
    assert f'WorkingDirectory="{project}"' in unit
    assert f'ExecStart="{project}/scripts/run-server.sh"' in unit
    assert "Restart=always" in unit
    assert "NoNewPrivileges=true" in unit
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "sudo loginctl enable-linger test-user" in result.stdout
    assert systemctl_log.read_text(encoding="utf-8").splitlines() == [
        "--user show-environment",
        "--user daemon-reload",
        "--user enable --now feishu-io.service",
    ]


def test_systemd_uninstaller_disables_service_but_keeps_project_data(tmp_path):
    project = tmp_path / "project"
    uninstaller = project / "scripts" / "uninstall-server-service.sh"
    _copy_executable(
        PROJECT_ROOT / "scripts" / "uninstall-server-service.sh",
        uninstaller,
    )
    config_home = tmp_path / "config"
    unit_file = config_home / "systemd" / "user" / "feishu-io.service"
    unit_file.parent.mkdir(parents=True)
    unit_file.write_text("[Service]\n", encoding="utf-8")
    env_file = project / ".env"
    data_file = project / "data" / "messages.sqlite3"
    env_file.write_text("secret\n", encoding="utf-8")
    data_file.parent.mkdir()
    data_file.write_text("data\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    systemctl_log = tmp_path / "systemctl.log"
    _write_executable(
        fake_bin / "systemctl",
        '#!/bin/sh\nprintf "%s\\n" "$*" >>"$SYSTEMCTL_LOG"\n',
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(config_home),
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "SYSTEMCTL_LOG": str(systemctl_log),
        }
    )

    result = subprocess.run(
        [str(uninstaller)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert not unit_file.exists()
    assert env_file.exists()
    assert data_file.exists()
    assert systemctl_log.read_text(encoding="utf-8").splitlines() == [
        "--user disable --now feishu-io.service",
        "--user daemon-reload",
        "--user reset-failed feishu-io.service",
    ]


def test_systemd_installer_stops_when_user_manager_is_unavailable(tmp_path):
    project = tmp_path / "project"
    installer = project / "scripts" / "install-server-service.sh"
    _copy_executable(
        PROJECT_ROOT / "scripts" / "install-server-service.sh",
        installer,
    )
    (project / ".env").write_text("FEISHU_IO_API_KEY=test\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    _write_executable(fake_bin / "systemctl", "#!/bin/sh\nexit 1\n")
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "USER": "test-user",
        }
    )

    result = subprocess.run(
        [str(installer)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "systemd user manager is unavailable" in result.stderr
    assert "loginctl enable-linger test-user" in result.stderr
    assert not (project / ".server-venv").exists()
