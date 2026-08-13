from pathlib import Path

from app.parser.sysmon_parser import SysMonParser
from app.services.wifi import normalize_clients, parse_site_survey
from app.services.wifi_clients import discover_vaps, parse_apstats, parse_wlanconfig_list

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_site_survey_synthetic_fixture():
    rows = parse_site_survey((FIXTURES / "site-survey-synthetic.log").read_text())
    assert rows == [
        {"ssid": "Lab-24", "bssid": "02:11:22:33:44:55", "channel": 1, "rssi": -50, "security": "WPA2"},
        {"ssid": "Lab-5", "bssid": "06:aa:bb:cc:dd:ee", "channel": 36, "rssi": -65, "security": "WPA3"},
    ]


def test_ssid_capability_fixture_emits_parser_event():
    events = []
    parser = SysMonParser(events.append)
    for line in (FIXTURES / "ssid-capability-synthetic.log").read_text().splitlines():
        parser.feed(line)
    assert events[-1]["type"] == "ssid_capability_update"
    assert events[-1]["capabilities"][1]["phy_mode"] == "11be"
    assert parser.ssid_capabilities[1]["mlo"] is True


def test_normalize_existing_wifi_clients_contract():
    snapshot = {"wifi_clients": {"5G": {"clients": [
        {"mac": "02:00:00:00:00:01", "txrate": "866M"}
    ]}}}
    rows = normalize_clients(snapshot)
    assert rows[0]["band"] == "5G"
    assert rows[0]["tx_rate"] == "866M"


def test_ported_wifi_client_parsers_with_format_faithful_text():
    iwconfig = (
        'ath16     IEEE 802.11axa  ESSID:"Lab-5"\n'
        "          Mode:Master  Frequency:5.18 GHz (Channel 36)"
    )
    assert discover_vaps(iwconfig)[0]["channel"] == 36
    listing = "02:11:22:33:44:55 1 36 866M 780M -48 00:12:03 IEEE80211_MODE_11AXA_HE80 2 2\nSNR: 44"
    station = parse_wlanconfig_list(listing, "ath16")[0]
    assert (station["mac"], station["txrate"], station["rssi"], station["snr"]) == (
        "02:11:22:33:44:55", "866M", -48, 44
    )
    stats = parse_apstats("Tx Data Bytes = 1234\nRx RSSI = -47\nchainmask (NSS) tx(2) rx(1)")
    assert (stats["tx_bytes"], stats["rx_rssi"], stats["tx_nss"], stats["rx_nss"]) == (1234, -47, 2, 1)
