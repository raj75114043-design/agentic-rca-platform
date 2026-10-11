import pytest

from rca.agents.graph import run_rca
from rca.llm import LLMClient


@pytest.mark.parametrize("cause", ["VENDOR_DELAY", "PERMIT_PENDING", "CREW_SHORTAGE", "FIBER_CUT", "HW_FAULT"])
def test_root_cause_identified(container, labels, cause):
    if cause not in labels:
        pytest.skip(f"no {cause} in reduced dataset")
    l = labels[cause]
    r = run_rca(container, f"Why is {l['site_id']} down or delayed?", persist=False)
    assert r["root_cause"] == cause and r["key_entity"] == l["key_entity"]
    assert r["confidence"] == "HIGH"


def test_vendor_delay_needs_multiple_iterations(container, labels):
    r = run_rca(container, f"Why is {labels['VENDOR_DELAY']['site_id']} delayed?", persist=False)
    assert r["iterations"] >= 2
    assert any(t["tool"] == "get_purchase_orders" for t in r["tool_log"])


def test_iteration_budget_is_respected(container, labels):
    r = run_rca(container, f"Why is {labels['VENDOR_DELAY']['site_id']} delayed?", persist=False)
    assert r["iterations"] <= container.settings.max_iterations


def test_no_duplicate_tool_calls(container, labels):
    r = run_rca(container, f"Why is {labels['HW_FAULT']['site_id']} down?", persist=False)
    sigs = [(t["tool"], str(t["args"])) for t in r["tool_log"]]
    assert len(sigs) == len(set(sigs))


def test_out_of_scope(container):
    r = run_rca(container, "Tell me a joke about cats", persist=False)
    assert r["intent"] == "out_of_scope" and r["root_cause"] is None


def test_unknown_site_handled(container):
    r = run_rca(container, "Why is SITE-9999 down?", persist=False)
    assert "could not find" in r["answer"].lower()


class FakeLLM(LLMClient):
    provider, model = "fake", "fake-1"

    def __init__(self, report: str):
        self.report = report

    @property
    def available(self):
        return True

    def chat(self, system, user, *, json_mode=False, usage=None, max_tokens=700):
        if usage:
            usage.calls += 1
        if "classify" in system:
            return {"intent": "site_rca"}
        if "Planner" in system:
            return {"steps": [{"tool": "rm_rf", "args": {}}]}  # invalid tool must be ignored
        return self.report


def test_llm_report_with_hallucinated_citation_is_rejected(container, labels):
    container.llm = FakeLLM("**Root cause**: vendor [po:PO-999999]")
    try:
        r = run_rca(container, f"Why is {labels['VENDOR_DELAY']['site_id']} delayed?", persist=False)
    finally:
        container.llm = LLMClient()
    assert r["answer_mode"] == "template" and "PO-999999" not in r["answer"]
    assert r["root_cause"] == "VENDOR_DELAY"  # invalid planner output fell back to analyst checks


def test_llm_report_with_valid_citations_is_used(container, labels):
    site = labels["PERMIT_PENDING"]["site_id"]
    container.llm = FakeLLM(f"**Root cause**: permit pending [site:{site}]")
    try:
        r = run_rca(container, f"Why is {site} delayed?", persist=False)
    finally:
        container.llm = LLMClient()
    assert r["answer_mode"] == "llm" and r["llm"]["calls"] >= 2


class ScriptedLLM(FakeLLM):
    """Returns successive reports, to exercise the corrective retry."""

    def __init__(self, reports):
        super().__init__("")
        self.reports = list(reports)

    def chat(self, system, user, *, json_mode=False, usage=None, max_tokens=700):
        if "Response agent" in system:
            if usage:
                usage.calls += 1
            return self.reports.pop(0)
        return super().chat(system, user, json_mode=json_mode, usage=usage, max_tokens=max_tokens)


def test_grouped_citations_are_accepted(container, labels):
    site = labels["VENDOR_DELAY"]["site_id"]
    container.llm = ScriptedLLM([f"**Root cause**: site delayed [site:{site}, site:{site}]"])
    try:
        r = run_rca(container, f"Why is {site} delayed?", persist=False)
    finally:
        container.llm = LLMClient()
    assert r["answer_mode"] == "llm"


def test_invalid_citation_is_corrected_on_retry(container, labels):
    site = labels["VENDOR_DELAY"]["site_id"]
    container.llm = ScriptedLLM(["vendor late [po:PO-999999]", f"site delayed [site:{site}]"])
    try:
        r = run_rca(container, f"Why is {site} delayed?", persist=False)
    finally:
        container.llm = LLMClient()
    assert r["answer_mode"] == "llm" and "PO-999999" not in r["answer"]


def test_template_recommends_resolution_steps(container, labels):
    for cause in ("HW_FAULT", "VENDOR_DELAY", "PERMIT_PENDING"):
        if cause in labels:
            r = run_rca(container, f"Why is {labels[cause]['site_id']} down?", persist=False)
            section = r["answer"].split("**Recommended actions**")[-1]
            assert "#Resolution]" in section, cause
