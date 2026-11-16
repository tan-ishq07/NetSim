"""Tests for the tutor eval scorer (ai/eval/score.py)."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "tutor_score",
    os.path.join(os.path.dirname(__file__), "..", "ai", "eval", "score.py"))
score = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(score)


def test_key_tokens_extracts_commands_ips_and_numbers():
    toks = score.key_tokens("Run 'no shutdown' on eth0; set ip address 192.168.1.10 255.255.255.0")
    assert "no" in toks and "shutdown" in toks
    assert "ip" in toks and "address" in toks
    assert "192.168.1.10" in toks and "255.255.255.0" in toks


def test_recall_is_one_when_prediction_contains_all_key_tokens():
    gold = "Fix: no shutdown on the interface."
    pred = "You should bring the interface up with 'no shutdown'."
    assert score.recall(gold, pred) == 1.0


def test_recall_is_low_when_prediction_misses_the_fix():
    gold = "Set remote-as 65002 on the BGP neighbor."
    pred = "It is a layer 2 problem unrelated to routing."
    assert score.recall(gold, pred) < 0.5


def test_score_records_breaks_down_by_pillar():
    recs = [
        {"gold": "no shutdown", "pred": "no shutdown", "pillar": "diagnosis"},
        {"gold": "use ip address", "pred": "type ip address", "pillar": "cli"},
        {"gold": "network 192.168.1.0", "pred": "wrong", "pillar": "knowledge"},
    ]
    out = score.score_records(recs)
    assert out["n"] == 3
    assert set(out["by_pillar"]) == {"diagnosis", "cli", "knowledge"}
    assert out["by_pillar"]["diagnosis"]["key_token_recall"] == 1.0
    assert 0.0 <= out["key_token_recall"] <= 1.0
