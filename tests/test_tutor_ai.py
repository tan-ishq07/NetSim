"""Tests for the tutor-AI data pipeline.

These enforce that the generated dataset is *grounded*: every injected fault
must actually break connectivity in the engine, every fix must reference the
right command, the CLI Q&A must match the live grammar, and computed knowledge
answers (subnetting) must be arithmetically correct."""
import ipaddress
import re

from app.engine import clihelp
from app.tutor_ai import cli_qa, dataset, generate, knowledge, mutators
from app.tutor_ai.symptoms import collect_symptoms
from app.workspace import Workspace


def _apply(spec):
    w = Workspace()
    w.apply_lab(spec)
    return w


def test_every_mutation_actually_breaks_a_working_lab():
    # Use a base that is healthy at baseline, then confirm each fault induces loss.
    base = dataset.gen_two_router_static(0)
    w = _apply(base)
    assert not collect_symptoms(w.net)["any_loss"], "baseline should be healthy"
    seen_kinds = set()
    for mutated, fault in mutators.mutations(base):
        w2 = _apply(mutated)
        sym = collect_symptoms(w2.net)
        assert sym["any_loss"], f"fault {fault['kind']} did not break connectivity"
        seen_kinds.add(fault["kind"])
    # the static two-router lab should exercise several fault kinds
    assert {"interface_shutdown", "missing_default_gateway", "wrong_ip_subnet"} <= seen_kinds


def test_fault_answer_mentions_the_fix_command():
    base = dataset.gen_router_lan(1)
    for mutated, fault in mutators.mutations(base):
        ex = dataset.example_from_fault(base, mutated, fault, "test")
        ans = ex["messages"][2]["content"].lower()
        if fault["kind"] == "interface_shutdown":
            assert "no shutdown" in ans
        if fault["kind"] == "missing_default_gateway":
            assert "default-gateway" in ans


def test_shutdown_fix_restores_connectivity():
    # The oracle fix must, when applied on top of the fault, actually heal the lab.
    base = dataset.gen_two_router_static(3)
    for mutated, fault in mutators.mutations(base):
        if fault["kind"] != "interface_shutdown":
            continue
        w = _apply(mutated)
        assert collect_symptoms(w.net)["any_loss"]
        sess = w.session(fault["device"])
        for line in fault["fix"]:
            sess.execute(line)
        assert not collect_symptoms(w.net)["any_loss"], "fix did not restore connectivity"
        break


def test_build_dataset_covers_core_fault_kinds():
    ds = dataset.build_dataset()
    kinds = {e["meta"]["kind"] for e in ds}
    assert {"interface_shutdown", "missing_default_gateway", "wrong_ip_subnet",
            "duplicate_ip", "ospf_area_mismatch", "bgp_remote_as_mismatch",
            "acl_missing_permit", "healthy"} <= kinds
    # every example is a well-formed chat triple
    for e in ds[:50]:
        roles = [m["role"] for m in e["messages"]]
        assert roles == ["system", "user", "assistant"]


def test_cli_qa_matches_grammar():
    qa = cli_qa.build_cli_qa()
    paths = {e["meta"].get("path") for e in qa if e["meta"]["kind"] == "describe"}
    # a few representative commands from across modes must be documented
    assert "ip address <A.B.C.D>" in paths or "ip address" in " ".join(p or "" for p in paths)
    assert any("switchport" in (p or "") for p in paths)
    # overview exists for every mode
    overview_modes = {e["meta"]["mode"] for e in qa if e["meta"]["kind"] == "overview"}
    assert set(clihelp.SPEC) == overview_modes


def test_subnetting_answers_are_correct():
    for ex in knowledge.subnetting_qa(40, seed=7):
        q = ex["messages"][1]["content"]
        a = ex["messages"][2]["content"]
        m = re.search(r"address (\d+\.\d+\.\d+\.\d+)/(\d+)", q)
        ip, prefix = m.group(1), int(m.group(2))
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        assert f"Network: {net.network_address}/{prefix}" in a
        assert f"Broadcast: {net.broadcast_address}" in a
        assert f"Mask: {net.netmask}" in a


def test_generate_build_all_is_deduped_and_split():
    rows = generate.build_all(seed=99, subnet_count=30, count_per_gen=3)
    assert len(rows) > 300
    keys = {(e["messages"][1]["content"], e["messages"][2]["content"]) for e in rows}
    assert len(keys) == len(rows), "dataset should be de-duplicated"
    pillars = {e["meta"]["pillar"] for e in rows}
    assert pillars == {"diagnosis", "cli", "knowledge"}
