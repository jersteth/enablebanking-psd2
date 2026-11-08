import copy
import json
from pathlib import Path

import jsonschema
import pytest

from history_docs import EXAMPLE, dump, example, scenarios

SCHEMA = json.loads((Path(__file__).parent.parent / "schemas" / "account-history.v1.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def validate(doc: dict) -> None:
    VALIDATOR.validate(doc)


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("name", sorted(scenarios()))
def test_scenarios_validate(name):
    validate(scenarios()[name])


def _broken(mutate):
    doc = copy.deepcopy(scenarios()["full_detail"])
    mutate(doc)
    with pytest.raises(jsonschema.ValidationError):
        validate(doc)


def test_extra_key_fails():
    _broken(lambda d: d["accounts"][0].update(surprise=1))
    _broken(lambda d: d["accounts"][0]["transactions"][0].update(surprise=1))
    _broken(lambda d: d.update(surprise=1))


def test_float_amount_fails():
    _broken(lambda d: d["accounts"][0]["transactions"][0].update(amount=-42.5))
    _broken(lambda d: d["accounts"][0]["transactions"][0].update(amount="42.50"))  # sign is required


def test_missing_key_or_bad_status_fails():
    _broken(lambda d: d["accounts"][0]["transactions"][0].pop("currency"))
    _broken(lambda d: d["accounts"][0].pop("api_calls"))
    _broken(lambda d: d["accounts"][0].update(status="partial"))
    _broken(lambda d: d.update(schema_version="2"))


def test_example_in_sync():
    assert EXAMPLE.read_text() == dump(example()), "run: .venv/bin/python tests/history_docs.py"
    validate(json.loads(EXAMPLE.read_text()))
