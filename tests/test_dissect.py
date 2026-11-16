import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import Network  # noqa: E402
from app.engine import dissect, pcap  # noqa: E402


def _two_hosts_one_switch():
    net = Network()
    h1 = net.add_host("PC1")
    h2 = net.add_host("PC2")
    net.add_switch("SW1", ports=4)
    h1.primary.set_ip("10.0.0.1", "24")
    h1.primary.enabled = True
    h2.primary.set_ip("10.0.0.2", "24")
    h2.primary.enabled = True
    net.connect("PC1", "eth0", "SW1", "FastEthernet0/0")
    net.connect("PC2", "eth0", "SW1", "FastEthernet0/1")
    return net


def test_capture_dict_enriches_with_layers_bytes_and_timing():
    net = _two_hosts_one_switch()
    res = net.ping("PC1", "10.0.0.2", 1)
    cap = res["capture"]
    assert cap, "expected captured frames"

    # ordering / numbering / monotonic synthesized time
    assert [c["no"] for c in cap] == list(range(1, len(cap) + 1))
    times = [c["time"] for c in cap]
    assert times == sorted(times)

    icmp = next(c for c in cap if c["protocol"] == "ICMP")
    # byte array matches the byte-accurate pcap synthesis and the reported length
    assert icmp["length"] == len(icmp["bytes"]) > 0
    assert icmp["protocols"] == ["eth", "ip", "icmp"]

    # layer tree: Frame -> Ethernet II -> IPv4 -> ICMP, with contiguous ranges
    names = [layer["name"].split(",")[0] for layer in icmp["layers"]]
    assert names[0].startswith("Frame")
    assert "Ethernet II" in icmp["layers"][1]["name"]
    assert "Internet Protocol Version 4" in icmp["layers"][2]["name"]
    assert icmp["layers"][3]["name"] == "Internet Control Message Protocol"

    # every layer range is within the frame and the last reaches the end
    for layer in icmp["layers"]:
        lo, hi = layer["range"]
        assert 0 <= lo <= hi <= icmp["length"]
    assert icmp["layers"][-1]["range"][1] == icmp["length"]


def test_capture_bytes_match_pcap_synthesis():
    net = _two_hosts_one_switch()
    net.ping("PC1", "10.0.0.2", 1)
    entries = net.last_capture
    enriched = dissect.capture_list(entries)
    for entry, d in zip(entries, enriched):
        assert d["bytes"] == list(pcap._frame_bytes(entry.frame))


def test_arp_dissects_to_two_layers():
    net = _two_hosts_one_switch()
    res = net.ping("PC1", "10.0.0.2", 1)
    arp = next(c for c in res["capture"] if c["protocol"] == "ARP")
    assert arp["protocols"] == ["eth", "arp"]
    assert any("Address Resolution Protocol" in layer["name"] for layer in arp["layers"])
