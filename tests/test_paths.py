import pytest

from enablebanking_psd2 import paths

NO_ENV: dict[str, str] = {}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def test_user_dirs_default_and_xdg(home, tmp_path):
    assert paths.user_config_dir(NO_ENV) == home / ".config" / "enablebanking-psd2"
    assert paths.user_state_dir(NO_ENV) == home / ".local" / "state" / "enablebanking-psd2"
    assert paths.user_data_dir(NO_ENV) == home / ".local" / "share" / "enablebanking-psd2"
    env = {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_STATE_HOME": str(tmp_path / "s"),
           "XDG_DATA_HOME": str(tmp_path / "d")}
    assert paths.user_config_dir(env) == tmp_path / "c" / "enablebanking-psd2"
    assert paths.user_state_dir(env) == tmp_path / "s" / "enablebanking-psd2"
    assert paths.user_data_dir(env) == tmp_path / "d" / "enablebanking-psd2"
    assert paths.user_config_dir({"XDG_CONFIG_HOME": "relative"}) == home / ".config" / "enablebanking-psd2"  # spec: ignore


def test_windows_uses_appdata(home, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    assert paths.user_state_dir({"APPDATA": str(tmp_path / "ad")}) == tmp_path / "ad" / "enablebanking-psd2"


def test_config_file_precedence(home, tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert paths.config_file(None, NO_ENV, cwd) is None                      # nothing anywhere: env-only operation
    user = paths.user_config_dir(NO_ENV) / "config.toml"
    user.parent.mkdir(parents=True)
    user.write_text("")
    assert paths.config_file(None, NO_ENV, cwd) == user                       # per-user default
    (cwd / "config.toml").write_text("")
    assert paths.config_file(None, NO_ENV, cwd) == cwd / "config.toml"        # legacy ./config.toml wins
    env = {"ENABLEBANKING_PSD2_CONFIG": str(tmp_path / "env.toml")}
    assert paths.config_file(None, env, cwd) == tmp_path / "env.toml"         # env beats ./ (returned even if missing)
    assert paths.config_file(tmp_path / "flag.toml", env, cwd) == tmp_path / "flag.toml"  # flag beats env


def test_default_config_target(home):
    assert paths.default_config_target(NO_ENV) == paths.user_config_dir(NO_ENV) / "config.toml"
    assert paths.default_config_target({"ENABLEBANKING_PSD2_CONFIG": "~/x.toml"}) == home / "x.toml"


def test_state_dir_precedence(home, tmp_path):
    cwd, base = tmp_path / "work", tmp_path / "cfg"
    cwd.mkdir()
    assert paths.state_dir(None, NO_ENV, cwd) == paths.user_state_dir(NO_ENV)
    (cwd / ".psd2demo").mkdir()
    assert paths.state_dir(None, NO_ENV, cwd) == cwd / ".psd2demo"            # legacy dir wins when it exists
    env = {"ENABLEBANKING_PSD2_STATE_DIR": "from-env"}
    assert paths.state_dir(None, env, cwd, base) == base / "from-env"         # env beats legacy; relative → config dir
    assert paths.state_dir("from-toml", env, cwd, base) == base / "from-toml"  # setting beats env
    assert paths.state_dir("rel", NO_ENV, cwd) == cwd / "rel"                  # no config file: relative to cwd
    assert paths.state_dir("~/st", NO_ENV, cwd, base) == home / "st"
    assert paths.state_dir(str(tmp_path / "abs"), NO_ENV, cwd, base) == tmp_path / "abs"


def test_export_dir_precedence(home, tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert paths.export_dir(None, NO_ENV, cwd) == paths.user_data_dir(NO_ENV) / "exports"
    (cwd / "exports").mkdir()
    assert paths.export_dir(None, NO_ENV, cwd) == cwd / "exports"
    assert paths.export_dir("out", {"ENABLEBANKING_PSD2_EXPORT_DIR": "x"}, cwd, tmp_path) == tmp_path / "out"


def test_in_git_worktree(tmp_path):
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    assert not paths.in_git_worktree(inner)
    (tmp_path / "a" / ".git").mkdir()
    assert paths.in_git_worktree(inner)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".git").write_text("gitdir: x")  # worktrees and submodules use a .git file
    assert paths.in_git_worktree(tmp_path / "other")
