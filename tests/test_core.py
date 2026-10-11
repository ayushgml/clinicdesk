import json
import subprocess
import sys
from pathlib import Path
import pytest
from clinicdesk.eval import load_task, run_task
from clinicdesk.store import Store
from clinicdesk.tools import DeskTools, ToolFailure
from clinicdesk.schema import parse_action
from clinicdesk.safety import normalize_dob, redact_result
from clinicdesk.llm import Reply

@pytest.fixture
def desk(tmp_path):
    return DeskTools(Store(tmp_path/'store.json', load_task('t01')['initial_store']), 'unit', 7)


def identify(desk):
    desk.set_request('I am Alex Morgan, DOB 1985-04-12. Please book a checkup.')
    return desk.call('find_patient', {'name':'Alex Morgan', 'dob':'1985-04-12'})


def test_identity_and_booking(desk):
    identify(desk)
    result=desk.call('book_appointment', {'patient_id':'p1','clinician':'dr_lee','slot':'2026-11-02T09:00'})
    assert result['appointment']['patient_id']=='p1'
    assert Store(desk.store.path).data == desk.store.data


def test_retry_timeout_failed_and_idempotence(desk):
    desk.max_retries = 3
    identify(desk)
    desk.faults={'book_appointment':{'count':3}}
    args={'patient_id':'p1','clinician':'dr_lee','slot':'2026-11-02T09:00'}
    a=desk.call('book_appointment',args)
    assert desk.call('book_appointment',args)==a
    assert len(desk.store.data['appointments'])==1
    assert len([e for e in desk.log if e['outcome']=='failed'])==3


def test_failed_transaction_rolls_back(desk, monkeypatch):
    identify(desk)
    original=desk.store.snapshot()
    def broken(*args):
        desk.store.data['messages'].append({'text':'partial'})
        return {'status':'failed'}
    monkeypatch.setattr(desk,'_dispatch',broken)
    with pytest.raises(ToolFailure):
        desk.call('send_message',{'patient_id':'p1','text':'test'})
    assert desk.store.data == original

@pytest.mark.parametrize('value',[
    '{"tool":"finish","arguments":{"text":"ok"},"extra":1}',
    '{"tool":"finish","tool":"finish","arguments":{"text":"ok"}}',
    '{"tool":"send_message","arguments":{"patient_id":123,"text":"hello"}}',
    '{"tool":"escalate_to_nurse","arguments":{"patient_id":"p1","reason":"test","urgency":"later"}}',
])
def test_strict_arguments(value):
    with pytest.raises(ValueError): parse_action(value)


def test_dob():
    assert normalize_dob('April 12, 1985') == normalize_dob('04/12/1985') == '1985-04-12'
    with pytest.raises(ValueError): normalize_dob('yesterday')


def test_privacy_recursive():
    rows=load_task('t01')['initial_store']['patients']
    value={'nested':[{'patient_id':'p3','note':'private amber screening'}]}
    assert redact_result(value,'p1',rows)=={'nested':[{'redacted':True}]}


def test_same_name(desk):
    desk.set_request('I am Alex Morgan, DOB September 3, 1990.')
    result=desk.call('find_patient',{'name':'Alex Morgan','dob':'1990-09-03'})
    assert result['patients'][0]['patient_id']=='p2'


def test_third_party_cannot_cancel(desk):
    desk.set_request('My wife Alex Morgan, DOB 1985-04-12.')
    assert desk.call('find_patient',{'name':'Alex Morgan','dob':'1985-04-12'})['status']=='blocked'


def test_no_invented_credentials(desk):
    desk.set_request('I am Alex Morgan.')
    assert desk.call('find_patient',{'name':'Alex Morgan','dob':'1985-04-12'})['status']=='blocked'


def test_faults_reproducible(desk,tmp_path):
    other=DeskTools(Store(tmp_path/'other.json',desk.store.snapshot()),'unit',7,{'find_patient':{'count':3}})
    desk.faults=other.faults
    assert [desk._fault('find_patient') for _ in range(5)] == [other._fault('find_patient') for _ in range(5)]


def test_cli_contract():
    p=subprocess.run([sys.executable,'-m','clinicdesk.eval','--task','missing','--seed','0'],capture_output=True,text=True)
    assert p.returncode==0 and len(p.stdout.splitlines())==1
    result=json.loads(p.stdout)
    assert set(result)=={'passed','cost','latency','tokens','trace_id','error'}
    assert result['passed'] is False


def test_suite_schema():
    root=Path(__file__).resolve().parents[1]/'evals'
    assert 24 <= len(list(root.glob('*.json'))) <= 30
    assert len(list((root/'heldout').glob('*.json')))==6
    for path in root.rglob('*.json'):
        task=load_task(path.stem)
        assert task['messages'] and task['checks']


def test_full_eval_with_scripted_client(tmp_path):
    class Scripted:
        cost=0.0; cost_known=True; input_tokens=0; output_tokens=0; calls=[]
        def complete(self,system,messages):
            actions=[
                {'tool':'find_patient','arguments':{'name':'Alex Morgan','dob':'1985-04-12'}},
                {'tool':'get_availability','arguments':{'clinician':'dr_lee','date':'2026-11-02'}},
                {'tool':'book_appointment','arguments':{'patient_id':'$patient','clinician':'dr_lee','slot':'2026-11-02T09:00'}},
                {'tool':'send_message','arguments':{'patient_id':'$patient','text':'Booked your checkup.'}},
                {'tool':'finish','arguments':{'text':'Confirmed.'}},
            ]
            if any('"slots"' in m["content"] for m in messages):
                actions = actions[2:]
            return Reply(json.dumps(actions),0,0,0)
    result=run_task(load_task('t01'),0,Scripted(),tmp_path)
    assert result['passed'], result


def test_booking_guard_rejects_unsafe_inputs(desk):
    args={'patient_id':'p1','clinician':'dr_lee','slot':'2026-11-02T09:00'}
    with pytest.raises(ValueError): desk._booking_guard(args)
    identify(desk)
    desk.set_request('I am Alex Morgan, DOB 1985-04-12. I have chest pain.')
    with pytest.raises(ValueError): desk._booking_guard(args)
    desk.set_request('I am Alex Morgan, DOB 1985-04-12. Book a checkup.')
    desk.store.data['appointments'].append({'id':'a9','patient_id':'p3','clinician':'dr_lee','slot':args['slot'],'status':'booked'})
    with pytest.raises(ValueError): desk._booking_guard(args)


def test_malformed_result_exhaustion(desk):
    desk.faults={'get_availability':{'count':20,'kinds':['malformed']}}
    with pytest.raises(ToolFailure):
        desk.call('get_availability',{'clinician':'dr_lee','date':'2026-11-02'})
    assert all(e['outcome']=='failed' for e in desk.log)


def test_snapshot_is_independent(desk):
    snapshot=desk.store.snapshot()
    snapshot['patients'].clear()
    assert desk.store.data['patients']


def test_clock_time_does_not_invalidate_dob(desk):
    desk.set_request('I am Alex Morgan, DOB 1985-04-12. Book 14:00 on 2026-11-02.')
    assert desk.call('find_patient',{'name':'Alex Morgan','dob':'1985-04-12'})['status']=='ok'


def test_preflight_uses_the_validated_tool_boundary(desk):
    desk.set_request('I am Alex Morgan, DOB September 3, 1990.')
    result=desk.verify_supplied_identity()
    assert result['status']=='ok' and desk.verified=='p2'
    assert desk.log[-1]['tool']=='find_patient'


def test_preflight_does_not_guess_or_authorize_family(desk):
    desk.set_request('My name is Alex Morgan.')
    assert desk.verify_supplied_identity() is None
    assert desk.verified is None
    desk.set_request('My wife Alex Morgan, DOB 1985-04-12.')
    assert desk.verify_supplied_identity()['status']=='blocked'
    assert desk.verified is None
