import argparse
from dataclasses import replace

from conftest import FakeClient, load_fixture, make_cfg, make_ctx
from enablebanking_psd2.commands import banks
from enablebanking_psd2.eb.models import Aspsp
from enablebanking_psd2.output import render_banks

ALL = [Aspsp.from_json(a) for a in load_fixture("aspsps.json")["aspsps"]]


def _banks(tmp_path, **kwargs):
    no_accounts = replace(make_cfg(tmp_path), accounts={})
    ctx, out, _ = make_ctx(tmp_path, FakeClient(aspsps=ALL), no_accounts)
    return banks(ctx, argparse.Namespace(**{"country": "BE", "filter": None, **kwargs})), out.getvalue()


def test_sorted_and_filtered_without_accounts(tmp_path):
    res, out = _banks(tmp_path)
    assert res.ok and [b["name"] for b in res.summary["banks"]] == ["Belfius", "ING", "KBC", "KBC Brussels"]
    res, _ = _banks(tmp_path, filter="kbc")
    assert [b["name"] for b in res.summary["banks"]] == ["KBC", "KBC Brussels"]
    kbc = res.summary["banks"][0]
    assert kbc == {"name": "KBC", "country": "BE", "bic": "KREDBEBB", "maximum_consent_validity_days": 180,
                   "required_psu_headers": [], "beta": False}


def test_render():
    text = render_banks(sorted(ALL, key=lambda b: b.name), width=200)
    lines = text.splitlines()
    assert lines[0].split() == ["Bank", "BIC", "Max", "consent", "Required", "PSU", "headers"]
    assert "Psu-Ip-Address, Psu-User-Agent" in text and "180 d" in text
    assert lines[3].rstrip().endswith("beta")  # ING
    assert render_banks([]) == "No banks match."


def test_country_defaults_to_the_configured_one(tmp_path):
    no_accounts = replace(make_cfg(tmp_path), accounts={}, country="NL")
    client = FakeClient(aspsps=ALL)
    ctx, _, _ = make_ctx(tmp_path, client, no_accounts)
    assert banks(ctx, argparse.Namespace(country=None, filter=None)).ok
    assert banks(ctx, argparse.Namespace(country="de", filter=None)).ok
    assert [c[1] for c in client.calls if c[0] == "aspsps"] == ["NL", "DE"]
