"""Where the config file, the state (sessions, cache) and the exports live.

Precedence for each: explicit setting > environment variable > the legacy project-local location, if it exists
(``./config.toml``, ``./.psd2demo``, ``./exports``) > the per-user default (XDG on POSIX, %APPDATA% on Windows).
Nothing here creates directories; the writers do, with mode 0700.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

APP = "enablebanking-psd2"
ENV_CONFIG = "ENABLEBANKING_PSD2_CONFIG"
ENV_STATE = "ENABLEBANKING_PSD2_STATE_DIR"
ENV_EXPORTS = "ENABLEBANKING_PSD2_EXPORT_DIR"
LEGACY_CONFIG, LEGACY_STATE, LEGACY_EXPORTS = "config.toml", ".psd2demo", "exports"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _user_dir(env: Mapping[str, str], xdg_var: str, fallback: str) -> Path:
    if _is_windows():
        return Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP
    base = env.get(xdg_var)
    return (Path(base).expanduser() if base and os.path.isabs(base) else Path.home() / fallback) / APP


def user_config_dir(env: Mapping[str, str]) -> Path:
    return _user_dir(env, "XDG_CONFIG_HOME", ".config")


def user_state_dir(env: Mapping[str, str]) -> Path:
    return _user_dir(env, "XDG_STATE_HOME", ".local/state")


def user_data_dir(env: Mapping[str, str]) -> Path:
    return _user_dir(env, "XDG_DATA_HOME", ".local/share")


def config_file(explicit: Path | None, env: Mapping[str, str], cwd: Path) -> Path | None:
    """The config file to read, or None when there is none (env-only operation). An explicit or env-named file
    is returned even if missing, so the caller can report it."""
    if explicit is not None:
        return explicit
    if env.get(ENV_CONFIG):
        return Path(env[ENV_CONFIG]).expanduser()
    for candidate in (cwd / LEGACY_CONFIG, user_config_dir(env) / "config.toml"):
        if candidate.is_file():
            return candidate
    return None


def default_config_target(env: Mapping[str, str]) -> Path:
    """Where ``init`` writes a new config."""
    return Path(env[ENV_CONFIG]).expanduser() if env.get(ENV_CONFIG) else user_config_dir(env) / "config.toml"


def _resolve(configured: str | None, env_var: str, env: Mapping[str, str], cwd: Path, base: Path | None,
             legacy: str, default: Path) -> Path:
    chosen = configured or env.get(env_var)
    if chosen:
        path = Path(chosen).expanduser()
        return path if path.is_absolute() else (base or cwd) / path  # relative: to the config file's directory
    return cwd / legacy if (cwd / legacy).exists() else default


def state_dir(configured: str | None, env: Mapping[str, str], cwd: Path, base: Path | None = None) -> Path:
    return _resolve(configured, ENV_STATE, env, cwd, base, LEGACY_STATE, user_state_dir(env))


def export_dir(configured: str | None, env: Mapping[str, str], cwd: Path, base: Path | None = None) -> Path:
    return _resolve(configured, ENV_EXPORTS, env, cwd, base, LEGACY_EXPORTS, user_data_dir(env) / "exports")


def in_git_worktree(path: Path) -> bool:
    """True when ``path`` or one of its parents contains a ``.git`` entry."""
    path = path.resolve()
    return any((p / ".git").exists() for p in (path, *path.parents))
