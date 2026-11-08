import importlib
import importlib.metadata
import subprocess
import sys
import zipfile

import enablebanking_psd2
from enablebanking_psd2 import cli
from enablebanking_psd2.contract import schema_path


def test_entry_points():
    scripts = {e.name: e.value for e in importlib.metadata.entry_points(group="console_scripts")
               if e.value.startswith("enablebanking_psd2")}
    assert scripts == {"enablebanking": "enablebanking_psd2.cli:main",
                       "enablebanking-psd2": "enablebanking_psd2.cli:main",
                       "psd2-demo": "enablebanking_psd2.cli:main"}


def test_version_is_single_sourced():
    assert importlib.metadata.version("enablebanking-psd2") == enablebanking_psd2.__version__


def test_shim_aliases_same_modules():
    code = (
        "import warnings; warnings.simplefilter('error', DeprecationWarning)\n"
        "try:\n    import psd2demo\nexcept DeprecationWarning:\n    print('warned')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "warned", out.stderr

    code = (
        "import warnings; warnings.simplefilter('ignore')\n"
        "import psd2demo.cli, psd2demo.eb.client as c, enablebanking_psd2.cli, enablebanking_psd2.eb.client as r\n"
        "assert psd2demo.cli is enablebanking_psd2.cli and c is r\n"
        "psd2demo.cli.marker = 1\nassert enablebanking_psd2.cli.marker == 1\nprint('same')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "same", out.stderr


def test_prog_is_one_of_our_names(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/bin/pytest"])
    assert cli._prog() == "enablebanking"
    monkeypatch.setattr(sys, "argv", ["/home/x/.local/bin/psd2-demo"])
    assert cli._prog() == "psd2-demo"


def test_schema_ships_in_package():
    assert schema_path().is_file()
    assert schema_path().read_text().lstrip().startswith("{")


def test_wheel_contains_schemas(tmp_path):
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path), "."], check=True,
                   capture_output=True)
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    assert "enablebanking_psd2/schemas/account-history.v1.json" in names
    assert "enablebanking_psd2/schemas/account-history.v1.example.json" in names
    assert "psd2demo/__init__.py" in names


def test_marketplace_has_required_fields():
    import json
    from pathlib import Path
    market = json.loads((Path(__file__).parent.parent / ".claude-plugin" / "marketplace.json").read_text())
    assert market["name"] and market["owner"]["name"] and market["plugins"][0]["source"] == "./"


def _workflow(name):
    import yaml
    from pathlib import Path
    return yaml.safe_load((Path(__file__).parent.parent / ".github" / "workflows" / name).read_text())


def test_release_workflow_uses_trusted_publishing_and_checks_the_tag():
    wf = _workflow("release.yml")
    publish = wf["jobs"]["publish"]
    assert publish["permissions"] == {"id-token": "write"} and publish["environment"] == "pypi"
    step = publish["steps"][-1]
    assert step["uses"].startswith("pypa/gh-action-pypi-publish@") and "with" not in step  # no password/token
    build_steps = " ".join(s.get("run", "") for s in wf["jobs"]["build"]["steps"])
    assert "GITHUB_REF_NAME" in build_steps and "__version__" in build_steps
    assert wf[True]["push"]["tags"] == ["v*"]  # PyYAML parses the key `on` as True


def test_ci_workflow_runs_tests_and_build_on_linux_and_macos():
    wf = _workflow("ci.yml")
    job = wf["jobs"]["test"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest"}
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "pytest" in runs and "python -m build" in runs and "twine check" in runs
