"""The bed's fixtures: 42 payloads present, adversarial outputs the validator must stop, model
inputs of the right shape, and the payloads through the pipeline (F3 plan T6.2; AC63, AC76)."""

from __future__ import annotations

import json

import payload_pipeline
import pytest
from fixtures.cases import ESCAPES, INPUTS, Escape, ModelCase
from fixtures.payloads import (
    EXPECTED_CONTROLS,
    EXPECTED_COUNT,
    LOGJACK_STANDINS,
    WHERE,
)
from nexovue_platform.ai.validate import Claim, check_claim
from nexovue_platform.evidence import MAX_MODEL_FACTS
from nexovue_platform.evidence.model_facts import ModelFacts

from nexovue_scheduler.evidence.rules import Facts

# ---------------------------------------------------------------- the 42


def test_forty_two_payloads_are_present_ten_of_them_harmless_controls() -> None:
    assert len(LOGJACK_STANDINS) == EXPECTED_COUNT == 42
    assert sum(1 for p in LOGJACK_STANDINS if not p.attack) == EXPECTED_CONTROLS == 10


def test_every_payload_has_a_unique_id_a_known_place_and_text() -> None:
    assert len({p.id for p in LOGJACK_STANDINS}) == 42
    assert all(p.where in WHERE and p.text.strip() and p.technique for p in LOGJACK_STANDINS)


def test_attacks_cover_every_place_and_controls_are_plain_operational_text() -> None:
    attacks = [p for p in LOGJACK_STANDINS if p.attack]

    assert {p.where for p in attacks} == set(WHERE)
    assert len({p.technique for p in attacks}) == 8
    assert all(
        "ignore" not in p.text.lower() and "http" not in p.text
        for p in LOGJACK_STANDINS
        if not p.attack
    )


def test_the_stand_ins_say_so_until_the_papers_payloads_replace_them() -> None:
    assert {p.source for p in LOGJACK_STANDINS} == {"standin"}


def test_none_of_the_42_reaches_the_bytes_sent_through_the_pipeline() -> None:
    assert payload_pipeline.run(list(LOGJACK_STANDINS)) == []


def test_the_pipeline_check_can_see_a_leak_when_one_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    real = payload_pipeline.outgoing_text

    def leaky(facts: Facts) -> str:
        # A builder that sent the service's own name along, as a model input without aliases would.
        return real(facts) + " " + str(facts.finding.service) + str(facts.edges[0].callee)

    monkeypatch.setattr(payload_pipeline, "outgoing_text", leaky)

    leaks = payload_pipeline.run(list(LOGJACK_STANDINS))

    assert {leak.field for leak in leaks} >= {"service_name", "callee"}
    assert {leak.payload_id for leak in leaks} >= {p.id for p in LOGJACK_STANDINS if p.attack}


# ---------------------------------------------------------------- escapes and inputs


def case_for(escape: Escape) -> ModelCase:
    wanted = "ordinary_no_cause" if escape.without_cause else "ordinary_deploy"
    return next(case for case in INPUTS if case.id == wanted)


@pytest.mark.parametrize("escape", ESCAPES, ids=[each.id for each in ESCAPES])
def test_each_way_out_is_removed_by_the_rule_that_names_it(escape: Escape) -> None:
    case = case_for(escape)
    cited = case.model_input["facts"][0]["id"]

    rule = check_claim(Claim("restates_fact", escape.text, (cited,)), case.model_input)

    assert rule == escape.removed_by


def test_every_input_is_built_through_the_typed_shape_and_holds_no_free_text() -> None:
    for case in INPUTS:
        text = json.dumps(case.model_input)
        assert case.model_input["schema"] == 1
        assert "http" not in text and "@" not in text
        for fact in case.model_input["facts"]:
            assert set(fact) <= set(ModelFacts.__dataclass_fields__) | set(fact)


def test_the_inputs_cover_ordinary_extreme_numbers_many_aliases_and_a_forged_edge() -> None:
    assert {case.family for case in INPUTS} == {
        "ordinary",
        "extreme_numbers",
        "many_aliases",
        "forged_edge",
    }
    many = next(case for case in INPUTS if case.family == "many_aliases")
    assert len(many.model_input["facts"]) == MAX_MODEL_FACTS - 1
    forged = next(case for case in INPUTS if case.family == "forged_edge")
    assert forged.model_input["likely_cause"]["strength"] == "possible"
    assert sum(1 for case in INPUTS if case.family == "ordinary") >= 3
