"""Phase 12: wireless (RF, 802.11 assoc/auth, WPA2/WPA3, CAPWAP/WLC, roaming, WMM)."""
from app.engine import labs, tutor, wireless
from app.engine.simulator import Network


def _ap(net, name, x, y, ssid="CorpWiFi", auth="wpa2-psk", psk="cisco123", band="2.4"):
    ap = net.add_ap(name, x, y)
    ap.band = band
    ap.ssids[ssid] = {"auth": auth, "psk": psk, "vlan": 10, "wmm": True}
    return ap


# --------------------------------------------------------------------------- #
# RF model
# --------------------------------------------------------------------------- #
def test_rssi_decreases_with_distance():
    net = Network()
    ap = _ap(net, "AP1", 0, 0)
    near = net.add_wifi_client("C-near", 5, 0)
    far = net.add_wifi_client("C-far", 80, 0)
    assert wireless.rssi(ap, near) > wireless.rssi(ap, far)


def test_coverage_radius_and_in_range():
    net = Network()
    ap = _ap(net, "AP1", 0, 0)
    r = wireless.coverage_radius(ap)
    assert r > 0
    inside = net.add_wifi_client("inside", r * 0.5, 0)
    outside = net.add_wifi_client("outside", r * 3, 0)
    assert wireless.in_range(ap, inside)
    assert not wireless.in_range(ap, outside)


def test_5ghz_has_more_loss_than_24ghz():
    net = Network()
    ap24 = _ap(net, "AP24", 0, 0, band="2.4")
    ap5 = _ap(net, "AP5", 0, 0, band="5")
    c = net.add_wifi_client("C", 20, 0)
    assert wireless.rssi(ap24, c) > wireless.rssi(ap5, c)


# --------------------------------------------------------------------------- #
# scan / associate / auth
# --------------------------------------------------------------------------- #
def test_scan_lists_in_range_aps_sorted_by_rssi():
    net = Network()
    _ap(net, "AP-close", 2, 0)
    _ap(net, "AP-far", 60, 0)
    c = net.add_wifi_client("C", 0, 0)
    c.profiles["CorpWiFi"] = "cisco123"
    results = wireless.scan(net, c)
    assert results[0]["ap"] == "AP-close"
    rssis = [r["rssi"] for r in results]
    assert rssis == sorted(rssis, reverse=True)


def test_wpa2_associate_runs_four_way_handshake():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="wpa2-psk", psk="cisco123")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "cisco123"
    res = wireless.associate(net, "C")
    assert res["ok"] is True
    assert res["key_installed"] is True
    assert "4way-msg4" in res["steps"]
    assert c.assoc["ap"] == "AP1"


def test_wpa2_wrong_psk_fails_auth():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="wpa2-psk", psk="cisco123")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "wrongpass"
    res = wireless.associate(net, "C")
    assert res["ok"] is False
    assert "PSK" in res["error"] or "auth" in res["error"].lower()
    assert c.assoc is None


def test_open_network_associates_without_keys():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="open", psk=None)
    net.add_wifi_client("C", 2, 0)
    res = wireless.associate(net, "C")
    assert res["ok"] is True
    assert res["key_installed"] is False
    assert res["authenticated"] is True


def test_wpa3_sae_sets_sae_flag():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="wpa3-sae", psk="dragonfly")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "dragonfly"
    res = wireless.associate(net, "C")
    assert res["ok"] is True
    assert res["sae"] is True
    assert "sae-commit" in res["steps"] and "sae-confirm" in res["steps"]


def test_associate_fails_when_out_of_range():
    net = Network()
    _ap(net, "AP1", 0, 0, auth="open", psk=None)
    net.add_wifi_client("C", 100000, 0)
    res = wireless.associate(net, "C")
    assert res["ok"] is False


# --------------------------------------------------------------------------- #
# CAPWAP / WLC
# --------------------------------------------------------------------------- #
def test_capwap_join_pushes_wlans_and_reaches_run():
    net = Network()
    wlc = net.add_wlc("WLC1")
    wlc.wlans["Corp"] = {"auth": "wpa2-psk", "psk": "cisco123", "vlan": 10}
    ap = net.add_ap("AP1", 0, 0)
    assert ap.capwap_state == "Idle"
    res = wireless.capwap_join(net, "AP1", "WLC1")
    assert res["ok"] is True
    assert ap.capwap_state == "Run"
    assert "Corp" in ap.ssids
    assert "AP1" in wlc.aps
    # a client can now join the controller-pushed SSID
    c = net.add_wifi_client("C", 1, 0)
    c.profiles["Corp"] = "cisco123"
    assert wireless.associate(net, "C")["ok"] is True


# --------------------------------------------------------------------------- #
# roaming
# --------------------------------------------------------------------------- #
def test_client_roams_to_stronger_ap_when_moving():
    net = Network()
    _ap(net, "AP-A", 0, 0, auth="open", psk=None)
    _ap(net, "AP-B", 100, 0, auth="open", psk=None)
    c = net.add_wifi_client("C", 5, 0)
    first = wireless.associate(net, "C")
    assert first["ap"] == "AP-A"
    # walk over near AP-B
    res = wireless.move(net, "C", 95, 0)
    assert res["roamed"] is True
    assert res["to"] == "AP-B"
    assert c.assoc["ap"] == "AP-B"
    assert len(c.roam_log) == 1


def test_no_roam_within_hysteresis():
    net = Network()
    _ap(net, "AP-A", 0, 0, auth="open", psk=None)
    _ap(net, "AP-B", 10, 0, auth="open", psk=None)
    net.add_wifi_client("C", 4, 0)
    wireless.associate(net, "C")
    res = wireless.move(net, "C", 5, 0)  # still basically between them
    assert res["roamed"] is False


# --------------------------------------------------------------------------- #
# WMM QoS
# --------------------------------------------------------------------------- #
def test_wmm_access_category_mapping():
    assert wireless.wmm_access_category(46) == "voice"
    assert wireless.wmm_access_category(34) == "video"
    assert wireless.wmm_access_category(0) == "best-effort"
    assert wireless.wmm_access_category(8) == "background"


# --------------------------------------------------------------------------- #
# data-plane gate
# --------------------------------------------------------------------------- #
def test_client_online_requires_keys_and_uplink():
    net = Network()
    ap = _ap(net, "AP1", 1, 0, auth="wpa2-psk", psk="cisco123")
    sw = net.add_switch("SW1", ports=2)
    net.connect("AP1", "GigabitEthernet0/0", "SW1", "FastEthernet0/0")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "cisco123"
    wireless.associate(net, "C")
    assert wireless.client_online(net, "C")["online"] is True
    # drop the AP wired uplink -> client no longer on the network
    ap.uplink.enabled = False
    assert wireless.client_online(net, "C")["online"] is False
    assert sw.name in net.devices


def test_lab_check_wifi_associated():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="wpa2-psk", psk="cisco123")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "cisco123"
    report = labs.grade_lab(net, [
        {"type": "wifi_associated", "name": "joins corp wifi", "client": "C",
         "ap": "AP1", "ssid": "CorpWiFi"},
    ])
    assert report["score"] == 100


def test_tutor_flags_wrong_wifi_credentials():
    net = Network()
    _ap(net, "AP1", 1, 0, auth="wpa2-psk", psk="cisco123")
    c = net.add_wifi_client("C", 2, 0)
    c.profiles["CorpWiFi"] = "wrong"
    wireless.associate(net, "C")  # fails -> remains unassociated
    findings = tutor.analyze(net)
    assert any("credentials" in f or "PSK" in f for f in findings) or \
        any("CorpWiFi" in f for f in findings)


def test_status_summarizes_devices():
    net = Network()
    _ap(net, "AP1", 0, 0, auth="open", psk=None)
    net.add_wifi_client("C", 1, 0)
    net.add_wlc("WLC1")
    wireless.associate(net, "C")
    st = wireless.status(net)
    assert len(st["aps"]) == 1 and len(st["clients"]) == 1 and len(st["wlcs"]) == 1
    assert st["aps"][0]["coverage_radius"] > 0
