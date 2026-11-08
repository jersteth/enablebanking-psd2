import json
from datetime import date

from conftest import FakeClient, cmd_args, make_ctx
from psd2demo.commands import run
from psd2demo.flow import Step


def _ctx(tmp_path):
    ctx, out, _ = make_ctx(tmp_path, FakeClient(), stored=True)
    return ctx, out


def run_args(**overrides):
    return cmd_args("run", **overrides)


def test_summary_and_render(tmp_path):
    ctx, out = _ctx(tmp_path)
    res = run(ctx, run_args(days=30))
    assert res.ok and res.summary["date_from"] == "2026-10-09" and res.summary["date_to"] == "2026-11-08"
    assert res.summary["account"] == "main" and res.summary["iban_masked"] == "BE68 **** **** 7034"
    assert res.summary["count"] == 4 and str(res.summary["totals"]["EUR"]["net"]) == "2447.51"
    assert "2026-10-09..2026-11-08" in out.getvalue()
    text = res.render()
    assert "Delhaize" in text and "Summary:" in text


def test_default_range_is_ten_days(tmp_path):
    ctx, _ = _ctx(tmp_path)
    assert run(ctx, run_args()).summary["date_from"] == "2026-10-29"
    assert ("transactions", "acc-uid-1", date(2026, 10, 29), date(2026, 11, 8)) in ctx.client.calls


def test_bad_range_is_config_failure(tmp_path):
    ctx, out = _ctx(tmp_path)
    res = run(ctx, run_args(days=5, date_to=date(2026, 11, 9)))
    assert res.failed_step is Step.CONFIG and res.exit_code == 2 and "either --days" in out.getvalue()


def test_unknown_account_is_config_failure(tmp_path):
    ctx, out = _ctx(tmp_path)
    res = run(ctx, run_args(account="ghost"))
    assert res.exit_code == 2 and "unknown account 'ghost'" in out.getvalue()


def test_export_inside_exports_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx, out = _ctx(tmp_path)
    res = run(ctx, run_args(export=tmp_path / "exports" / "run.json"))
    assert res.ok and "outside exports/" not in out.getvalue()
    assert json.loads((tmp_path / "exports" / "run.json").read_text())["meta"]["account"] == "main"


def test_export_outside_exports_dir_warns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx, out = _ctx(tmp_path)
    assert run(ctx, run_args(export=tmp_path / "run.json")).ok
    assert "outside exports/, so it is not git-ignored" in out.getvalue()


def test_export_failure_keeps_render(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "blocker").write_text("")
    ctx, out = _ctx(tmp_path)
    res = run(ctx, run_args(export=tmp_path / "blocker" / "run.json"))
    assert res.failed_step is Step.EXPORT and res.exit_code == 1
    assert "Delhaize" in res.render() and res.summary["count"] == 4
