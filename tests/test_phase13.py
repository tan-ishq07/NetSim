"""Phase 13: WAN/SD-WAN/QoS/cloud (PPP, QoS, SD-WAN, VXLAN/EVPN, MPLS/SR)."""
import pytest

from app.engine import wan
from app.engine.wan import SdwanPath, Tloc, VxlanFabric


# --------------------------------------------------------------------------- #
# PPP / PPPoE
# --------------------------------------------------------------------------- #
def test_ppp_no_auth_comes_up():
    res = wan.ppp_negotiate("none")
    assert res["up"] is True
    assert res["phase"] == "IPCP-open"
    assert "IPCP-ConfAck" in res["steps"]


def test_ppp_chap_success_and_failure():
    users = {"R2": "secret"}
    ok = wan.ppp_negotiate("chap", {"username": "R2", "password": "secret"}, users)
    assert ok["up"] is True
    assert "auth-chap-ack" in ok["steps"]
    bad = wan.ppp_negotiate("chap", {"username": "R2", "password": "nope"}, users)
    assert bad["up"] is False
    assert bad["phase"] == "terminate"


def test_pppoe_runs_discovery_then_ppp():
    res = wan.pppoe_session("pap", {"username": "u", "password": "p"}, {"u": "p"})
    assert res["discovery"] == ["PADI", "PADO", "PADR", "PADS"]
    assert res["up"] is True


# --------------------------------------------------------------------------- #
# QoS
# --------------------------------------------------------------------------- #
def test_classify_first_match_then_default():
    classes = [
        {"name": "VOICE", "match": {"dscp": 46}},
        {"name": "WEB", "match": {"protocol": "tcp", "port": 80}},
    ]
    assert wan.classify({"dscp": 46}, classes) == "VOICE"
    assert wan.classify({"protocol": "tcp", "port": 80}, classes) == "WEB"
    assert wan.classify({"protocol": "udp"}, classes) == "class-default"


def test_mark_dscp_values():
    assert wan.mark_dscp("ef") == 46
    assert wan.mark_dscp("af31") == 26
    assert wan.mark_dscp("unknown") == 0


def test_police_token_bucket_drops_excess_burst():
    # 1 Mbps CIR, 1500-byte bucket; three back-to-back 1500B packets at t=0
    pkts = [(0.0, 1500), (0.0, 1500), (0.0, 1500)]
    res = wan.police(pkts, cir_bps=1_000_000, bc_bytes=1500)
    assert res["conform"] == 1
    assert res["exceed"] == 2


def test_police_refills_over_time():
    # spaced out enough that tokens refill -> all conform
    pkts = [(0.0, 1250), (1.0, 1250), (2.0, 1250)]
    res = wan.police(pkts, cir_bps=10_000, bc_bytes=1250)  # 10kbps = 1250 B/s
    assert res["conform"] == 3
    assert res["exceed"] == 0


def test_llq_priority_drains_before_weighted():
    queues = [
        {"name": "VOICE", "priority": True, "packets": 2},
        {"name": "DATA", "weight": 1, "packets": 2},
        {"name": "BULK", "weight": 1, "packets": 2},
    ]
    order = wan.llq_schedule(queues, rounds=2)
    assert order[:2] == ["VOICE", "VOICE"]  # strict priority first
    assert set(order[2:]) == {"DATA", "BULK"}


# --------------------------------------------------------------------------- #
# SD-WAN
# --------------------------------------------------------------------------- #
def test_bfd_state_tracks_transport_and_admin():
    p = SdwanPath(Tloc("1.1.1.1", "mpls"))
    assert wan.bfd_state(p, transport_up=True) == "up"
    assert wan.bfd_state(p, transport_up=False) == "down"
    assert wan.bfd_state(SdwanPath(Tloc("1.1.1.1", "lte"), up=False)) == "down"


def test_omp_best_path_prefers_preference_then_latency():
    paths = [
        SdwanPath(Tloc("1.1.1.1", "mpls"), latency=30, preference=10),
        SdwanPath(Tloc("2.2.2.2", "biz-internet"), latency=10, preference=10),
        SdwanPath(Tloc("3.3.3.3", "lte"), latency=5, preference=0),
    ]
    best = wan.omp_best_path(paths)
    assert best.tloc.color == "biz-internet"  # tie pref 10, lower latency wins


def test_app_aware_route_prefers_color_meeting_sla():
    paths = [
        SdwanPath(Tloc("1.1.1.1", "mpls"), loss=0.1, latency=20, jitter=2),
        SdwanPath(Tloc("2.2.2.2", "biz-internet"), loss=2.0, latency=40, jitter=10),
    ]
    sla = {"max_loss": 1.0, "max_latency": 150, "max_jitter": 30}
    res = wan.app_aware_route(paths, sla, preferred_colors=["biz-internet", "mpls"])
    # biz-internet fails loss SLA -> falls through to mpls
    assert res["chosen"].tloc.color == "mpls"
    assert res["sla_met"] is True


def test_app_aware_route_brownout_fallback():
    paths = [SdwanPath(Tloc("1.1.1.1", "mpls"), loss=5.0, latency=300, jitter=50)]
    sla = {"max_loss": 1.0, "max_latency": 150, "max_jitter": 30}
    res = wan.app_aware_route(paths, sla)
    assert res["chosen"].tloc.color == "mpls"  # least-bad fallback
    assert res["sla_met"] is False
    assert res["fallback"] is True


def test_app_aware_route_none_when_all_down():
    paths = [SdwanPath(Tloc("1.1.1.1", "mpls"), up=False)]
    res = wan.app_aware_route(paths, {})
    assert res["chosen"] is None


# --------------------------------------------------------------------------- #
# VXLAN / EVPN
# --------------------------------------------------------------------------- #
def test_evpn_learn_and_unicast_forward():
    f = VxlanFabric()
    f.add_vtep("LEAF1", "10.0.0.1", [10010])
    f.add_vtep("LEAF2", "10.0.0.2", [10010])
    f.evpn_advertise("LEAF2", 10010, "AAAA.BBBB.CCCC")
    res = f.forward("LEAF1", 10010, "aaaa.bbbb.cccc")
    assert res["action"] == "unicast"
    assert res["encap"]["outer_dst"] == "10.0.0.2"
    assert res["encap"]["vni"] == 10010


def test_vxlan_floods_unknown_unicast():
    f = VxlanFabric()
    f.add_vtep("LEAF1", "10.0.0.1", [20020])
    f.add_vtep("LEAF2", "10.0.0.2", [20020])
    f.add_vtep("LEAF3", "10.0.0.3", [20020])
    res = f.forward("LEAF1", 20020, "DEAD.BEEF.0001")
    assert res["action"] == "flood"
    assert res["vteps"] == ["LEAF2", "LEAF3"]


# --------------------------------------------------------------------------- #
# MPLS / SR
# --------------------------------------------------------------------------- #
def test_ldp_lsp_push_swap_pop_with_php():
    lsp = wan.ldp_lsp(["PE1", "P1", "P2", "PE2"], fec="10.2.2.0/24")
    ops = [h["op"] for h in lsp["hops"]]
    assert ops == ["push", "swap", "pop"]  # ingress push, core swap, penultimate pop
    assert lsp["hops"][-1]["out_label"] == "implicit-null"
    assert lsp["egress"] == "PE2"


def test_ldp_lsp_without_php_swaps_to_egress_label():
    lsp = wan.ldp_lsp(["PE1", "P1", "PE2"], fec="x", php=False)
    assert lsp["hops"][-1]["op"] == "swap"
    assert lsp["hops"][-1]["out_label"] != "implicit-null"


def test_sr_resolve_builds_label_stack():
    node_sids = {"P1": 16001, "PE2": 16002}
    sids = [
        {"type": "node", "node": "P1"},
        {"type": "adj", "label": 24012},
        {"type": "node", "node": "PE2"},
    ]
    res = wan.sr_resolve(sids, node_sids)
    assert res["label_stack"] == [16001, 24012, 16002]
    assert res["top_label"] == 16001
    assert res["segments"] == 3


def test_sr_resolve_rejects_bad_sid():
    with pytest.raises(ValueError):
        wan.sr_resolve([{"type": "bogus"}], {})
