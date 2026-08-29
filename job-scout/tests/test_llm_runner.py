import json

import pytest

import llm_runner


def test_call_json_returns_parsed_dict(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        class P: stdout = '  {"role_type_match": "pass"}  \n'
        return P()

    monkeypatch.setattr(llm_runner.subprocess, 'run', fake_run)
    result = llm_runner.call_json('classify this', model='claude-haiku-4-5')
    assert result == {'role_type_match': 'pass'}
    assert captured['cmd'][:3] == ['hermes', 'chat', '-Q']
    assert '-m' in captured['cmd']
    assert 'claude-haiku-4-5' in captured['cmd']


def test_call_json_strips_prose_around_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        class P: stdout = 'Sure! Here you go:\n{"role_type_match":"exclude"}\nThat is all.'
        return P()
    monkeypatch.setattr(llm_runner.subprocess, 'run', fake_run)
    assert llm_runner.call_json('x') == {'role_type_match': 'exclude'}


def test_call_json_raises_on_no_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        class P: stdout = 'I cannot help with that'
        return P()
    monkeypatch.setattr(llm_runner.subprocess, 'run', fake_run)
    with pytest.raises(ValueError):
        llm_runner.call_json('x')
