from types import SimpleNamespace as NS
import pytest
from clinicdesk.llm import LLM


def client(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','unit-test-key')
    monkeypatch.setenv('LLM_PROVIDER','openrouter')
    monkeypatch.setenv('VICTIM_MODEL','qwen/qwen3.8-flash')
    return LLM()


def response():
    return NS(id='test-response', choices=[NS(message=NS(content='[]'))], usage=NS(prompt_tokens=100,completion_tokens=10,cost=0.00002))


def test_cost_and_temperature_retry(monkeypatch):
    llm=client(monkeypatch)
    calls=[]
    class Rejected(Exception): status_code=400
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls)==1: raise Rejected('temperature unsupported')
        return response()
    monkeypatch.setattr(llm.client.chat.completions,'create',create)
    r=llm.complete('system',[{'role':'user','content':'hello'}])
    assert calls[0]['temperature']==0 and 'temperature' not in calls[1]
    assert r.cost == llm.cost == .00002
    assert (llm.input_tokens,llm.output_tokens)==(100,10)


def test_non_temperature_error_not_retried(monkeypatch):
    llm=client(monkeypatch)
    calls=[]
    class Rejected(Exception): status_code=400
    def create(**kwargs):
        calls.append(kwargs); raise Rejected('invalid model')
    monkeypatch.setattr(llm.client.chat.completions,'create',create)
    with pytest.raises(Rejected): llm.complete('system',[])
    assert len(calls)==1


def test_transport_cost_unknown(monkeypatch):
    llm=client(monkeypatch)
    def create(**kwargs): raise TimeoutError('network')
    monkeypatch.setattr(llm.client.chat.completions,'create',create)
    with pytest.raises(TimeoutError): llm.complete('system',[])
    assert not llm.cost_known
