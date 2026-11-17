"""Native RPC and durable-tool integration tests, without a model or GPUs."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controller_tools import ControllerTools
from experiment_progress import ExperimentProgress
from persistent_meta import (
    CodexAppServer,
    ExperimentJobs,
    NativeExperimentTools,
    dynamic_tools,
    native_thread_parameters,
    process_identity,
    write_json,
)


def test_real_process_survives_native_tool_error_and_multiple_turns(tmp_path, monkeypatch):
    executable = tmp_path / 'fake_codex'
    executable.write_text('''#!/usr/bin/env python3
import json, sys
def send(obj):
    print(json.dumps(obj), flush=True)
turn=0
calls=0
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get('method')
    if method=='initialize':
        send({'id':msg['id'],'result':{}})
    elif method=='thread/start':
        send({'id':msg['id'],'result':{'thread':{'id':'thread'}}})
    elif method=='turn/start':
        assert msg['params']['environments'][0]['environmentId']=='local'
        turn+=1
        calls=0
        send({'id':msg['id'],'result':{'turn':{'id':str(turn)}}})
        send({'id':'call-0','method':'item/tool/call','params':{'tool':'probe','arguments':{},'callId':'0'}})
    elif 'result' in msg and str(msg.get('id','')).startswith('call-'):
        calls+=1
        if calls<3:
            send({'id':'call-'+str(calls),'method':'item/tool/call','params':{'tool':'probe','arguments':{},'callId':str(calls)}})
        else:
            send({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':str(turn),'status':'completed'}}})
''')
    executable.chmod(0o755)
    monkeypatch.setenv('OPENAI_API_KEY', 'parent-only-secret')
    launches = []

    class Launcher:
        def spawn(self, argv, cwd, stdin, stdout, stderr):
            launches.append({'argv': argv, 'cwd': cwd})
            return subprocess.Popen(
                argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr,
                text=True, bufsize=1,
                env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path)},
            )

    pids = []
    def probe(*args):
        pids.append(server.process.pid)
        if len(pids) == 1:
            raise RuntimeError('recoverable tool error')
        return {'status': 'ok'}
    server = CodexAppServer(codex_home=tmp_path, workspace=tmp_path, journal=tmp_path/'rpc',
                            executable=str(executable), on_tool=probe, process_launcher=Launcher())
    try:
        server.start()
        server.request('thread/start', {'dynamicTools': dynamic_tools()})
        environments = [{'environmentId':'local','cwd':str(tmp_path)}]
        assert server.turn('thread', 'first', environments=environments)['status'] == 'completed'
        assert server.turn('thread', 'second', environments=environments)['status'] == 'completed'
        assert len(pids) == 6 and len(set(pids)) == 1
        assert server.process.poll() is None
        assert len(launches) == 1
    finally:
        server.close()


def test_durable_job_reuses_process_and_stores_result_separately(tmp_path):
    project = tmp_path/'project'
    project.mkdir()
    (project/'controller_tools.py').write_text('''import json,sys,time
from pathlib import Path
request=json.load(sys.stdin)
while not Path(request['release']).exists(): time.sleep(.02)
print('noisy stdout, not JSON',flush=True)
Path(sys.argv[sys.argv.index('--result-file')+1]).write_text(json.dumps({'operation':'run_parent','status':'completed'}))
''')
    run=tmp_path/'run'
    jobs=ExperimentJobs(project,run)
    request={'operation':'run_parent','release':str(tmp_path/'release')}
    turn=run/'meta_session/turns/0000'
    write_json(turn/'command.json', {'action':'tool','request':request})
    first=jobs.start(request,turn)
    try:
        assert first['status']=='running'
        assert jobs.start(request,turn)['pid']==first['pid']
        assert process_identity(first['pid']) is not None
        # A new bridge attaches to the exact live worker without starting a second rollout.
        migrated=ExperimentJobs(project,run)
        adopted=migrated.adopt_legacy({'status':'tool_inflight','turn':0})
        assert adopted[0]['pid']==first['pid']
        assert migrated.status(first['job_id'])['status']=='running'
        (tmp_path/'release').touch()
        result=migrated.wait(first['job_id'],seconds=5)
        assert result['status']=='completed'
        assert result['result']['status']=='completed'
        assert json.loads((turn/'receipt.json').read_text())['status']=='completed'
        assert 'noisy stdout' in Path(result['stdout_path']).read_text()
    finally:
        child=jobs.children[first['job_id']]
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


def test_running_receipt_does_not_complete_rollout_milestone(tmp_path):
    session=tmp_path/'meta_session'
    turn=session/'turns/0000'
    write_json(turn/'command.json',{'action':'tool','request':{'operation':'run_parent','round_number':1}})
    write_json(turn/'receipt.json',{'operation':'run_parent','status':'running','job_id':'job'})
    progress=ExperimentProgress(session)
    state=progress.rebuild()
    assert 'parent_rollout' not in state['stages'][1]['completed_milestones']
    write_json(turn/'receipt.json',{'operation':'run_parent','status':'completed'})
    assert 'parent_rollout' in progress.rebuild()['stages'][1]['completed_milestones']


def test_native_errors_return_to_same_tools_without_losing_ledger(tmp_path):
    run=tmp_path/'run'
    executor=ControllerTools(workspace=tmp_path,receipt_root=run/'receipts')
    tools=NativeExperimentTools(project=tmp_path,run=run,executor=executor,
                                progress=ExperimentProgress(run/'meta_session'))
    failed=tools('experiment',{'request':{'operation':'read_json','path':str(run/'missing.json')}})
    assert 'tool_error' in failed
    ok=tools('experiment',{'request':{'operation':'write_text','path':str(run/'candidate/helper.py'),'content':'x=1'}})
    assert 'written' in ok and (run/'candidate/helper.py').read_text()=='x=1'
    fixed=tools('experiment',{'request':{'operation':'write_text','path':str(tmp_path/'controller_tools.py'),'content':'# repair'}})
    assert 'written' in fixed and (tmp_path/'controller_tools.py').read_text()=='# repair'
    outside=tools('experiment',{'request':{'operation':'write_text','path':'/outside-rsiflow.py','content':'no'}})
    assert 'tool_error' in outside and 'workspace' in outside
    finish=tools('finish_experiment',{'summary':'premature'})
    assert 'incomplete' in finish and not tools.finished


def test_new_operations_are_forwarded_without_bridge_whitelist(tmp_path):
    class Executor:
        def execute(self, request):
            assert request['operation']=='new_debug_helper'
            return {'status':'diagnosed','detail':'new implementation'}
    run=tmp_path/'run'
    tools=NativeExperimentTools(project=tmp_path,run=run,executor=Executor(),
                                progress=ExperimentProgress(run/'meta_session'))
    result=tools('experiment',{'request':{'operation':'new_debug_helper'}})
    assert 'diagnosed' in result and 'unknown_operation' not in result


def test_durable_host_jobs_strip_provider_credentials(tmp_path, monkeypatch):
    import persistent_meta

    project = tmp_path / 'project'
    project.mkdir()
    run = tmp_path / 'run'
    received = {}

    class Child:
        pid = os.getpid()

        def poll(self):
            return None

    def popen(argv, **kwargs):
        received.update(kwargs['env'])
        return Child()

    monkeypatch.setenv('OPENAI_API_KEY', 'parent-only-secret')
    monkeypatch.setenv('AUTODL_API_KEY', 'legacy-parent-only-secret')
    monkeypatch.setattr(persistent_meta.subprocess, 'Popen', popen)
    jobs = ExperimentJobs(project, run)
    jobs.start({'operation': 'run_parent'}, run / 'meta_session/turns/0000')
    assert 'OPENAI_API_KEY' not in received
    assert 'AUTODL_API_KEY' not in received


def test_native_coding_config_uses_configured_meta_provider(tmp_path):
    params=native_thread_parameters(tmp_path, {'model':'gpt-6-astra', 'provider':'OpenAI'})
    assert params['model']=='gpt-6-astra'
    assert params['modelProvider']=='OpenAI'
    assert params['sandbox']=='workspace-write' and params['cwd']==str(tmp_path)
    config=params['config']
    assert config['features.shell_tool'] is True
    assert config['features.use_legacy_landlock'] is True
    assert config['features.multi_agent'] is False
    assert config['features.multi_agent_v2'] is False
    assert config['sandbox_workspace_write.network_access'] is False
    assert config['sandbox_workspace_write.exclude_slash_tmp'] is True
    assert config['sandbox_workspace_write.exclude_tmpdir_env_var'] is True
    assert 'environments' not in params
    assert 'supersedes earlier' in params['developerInstructions']
    assert 'add tools within the rsiH workspace' in params['developerInstructions']


def test_persistent_meta_uses_namespace_codex_and_closes_sandbox(tmp_path, monkeypatch):
    import persistent_meta
    import persistent_meta_sandbox

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    (session / 'state.json').write_text(json.dumps({
        'transport': 'app_server', 'status': 'running', 'thread_id': 'thread-existing',
        'sandbox_backend': 'linux_bwrap_seccomp_v1',
    }))
    created = {}

    class Sandbox:
        def __init__(self, **kwargs):
            created['sandbox_kwargs'] = kwargs
            created['closed'] = False

        def identity(self):
            return {'backend': 'linux_bwrap_seccomp_v1'}

        def close(self):
            created['closed'] = True

    class Server:
        def __init__(self, **kwargs):
            created['server_kwargs'] = kwargs
            self.process = type('Process', (), {'pid': 12345})()

        def start(self):
            return self

        def request(self, method, params):
            assert method == 'thread/resume'
            assert params['threadId'] == 'thread-existing'
            return {'thread': {'id': 'thread-existing'}}

        def close(self):
            created['server_closed'] = True

    monkeypatch.setattr(persistent_meta_sandbox, 'PersistentMetaSandbox', Sandbox)
    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'},
        sandbox_backend='linux_bwrap_seccomp_v1', writable_outputs=(run / 'declared-output',),
    )
    loop.tools.finished = True
    result = loop.run_experiment('continue')
    assert result['status'] == 'complete' and result['thread_id'] == 'thread-existing'
    assert created['server_kwargs']['executable'] == '/codex'
    assert isinstance(created['server_kwargs']['process_launcher'], Sandbox)
    assert created['sandbox_kwargs']['writable_outputs'] == (run / 'declared-output',)
    assert created['server_closed'] is True and created['closed'] is True


def test_start_failure_persists_selected_backend_before_server_start(tmp_path, monkeypatch):
    import persistent_meta
    import persistent_meta_sandbox

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    observed = []

    class Sandbox:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    class Server:
        def __init__(self, **kwargs):
            pass

        def start(self):
            state_path = session / 'state.json'
            observed.append(json.loads(state_path.read_text()) if state_path.exists() else {})
            raise RuntimeError('controlled start failure')

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta_sandbox, 'PersistentMetaSandbox', Sandbox)
    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'},
        sandbox_backend='linux_bwrap_seccomp_v1',
    )
    with pytest.raises(RuntimeError, match='controlled start failure'):
        loop.run_experiment('start')
    assert observed[0]['sandbox_backend'] == 'linux_bwrap_seccomp_v1'
    state = json.loads((session / 'state.json').read_text())
    assert state['status'] == 'disconnected'
    assert state['sandbox_backend'] == 'linux_bwrap_seccomp_v1'
    assert state['last_error'] == 'RuntimeError: controlled start failure'


def test_legacy_start_failure_persists_legacy_backend_before_server_start(tmp_path, monkeypatch):
    import persistent_meta

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    observed = []

    class Server:
        def __init__(self, **kwargs):
            pass

        def start(self):
            state_path = session / 'state.json'
            observed.append(json.loads(state_path.read_text()) if state_path.exists() else {})
            raise RuntimeError('controlled legacy start failure')

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'}, sandbox_backend=None,
    )
    with pytest.raises(RuntimeError, match='controlled legacy start failure'):
        loop.run_experiment('start')
    assert observed[0]['sandbox_backend'] == 'legacy_host'
    state = json.loads((session / 'state.json').read_text())
    assert state['status'] == 'disconnected'
    assert state['sandbox_backend'] == 'legacy_host'


def test_legacy_resume_without_recorded_backend_stays_legacy(tmp_path, monkeypatch):
    import persistent_meta

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    (session / 'state.json').write_text(json.dumps({
        'transport': 'app_server', 'status': 'running', 'thread_id': 'thread-existing',
    }))
    requests = []

    class Server:
        def __init__(self, **kwargs):
            self.process = type('Process', (), {'pid': 12345})()

        def start(self):
            return self

        def request(self, method, params):
            requests.append((method, params))
            return {'thread': {'id': 'thread-existing'}}

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'}, sandbox_backend=None,
    )
    loop.tools.finished = True
    result = loop.run_experiment('continue')
    assert result['status'] == 'complete'
    assert result['sandbox_backend'] == 'legacy_host'
    assert requests[0][0] == 'thread/resume'
    assert json.loads((session / 'state.json').read_text())['sandbox_backend'] == 'legacy_host'


def test_bwrap_resume_rejects_legacy_state_missing_backend_before_any_construction(tmp_path, monkeypatch):
    import persistent_meta
    import persistent_meta_sandbox

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    state_path = session / 'state.json'
    original = json.dumps({
        'transport': 'app_server', 'status': 'disconnected', 'thread_id': None,
        'last_error': 'OSError: [Errno 38] Function not implemented',
    }) + '\n'
    state_path.write_text(original)
    constructed = []

    class Server:
        def __init__(self, **kwargs):
            constructed.append(('server', kwargs))

    class Sandbox:
        def __init__(self, **kwargs):
            constructed.append(('sandbox', kwargs))

    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    monkeypatch.setattr(persistent_meta_sandbox, 'PersistentMetaSandbox', Sandbox)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'},
        sandbox_backend='linux_bwrap_seccomp_v1',
    )
    with pytest.raises(RuntimeError, match='recorded=legacy_host, selected=linux_bwrap_seccomp_v1'):
        loop.run_experiment('continue')
    assert constructed == []
    assert state_path.read_text() == original


def test_legacy_resume_rejects_recorded_bwrap_before_server_construction(tmp_path, monkeypatch):
    import persistent_meta

    project = tmp_path / 'repository/RSIFlow'
    project.mkdir(parents=True)
    run = tmp_path / 'run'
    session = run / 'meta_session'
    session.mkdir(parents=True)
    (session / 'state.json').write_text(json.dumps({
        'transport': 'app_server', 'status': 'running', 'thread_id': 'thread-existing',
        'sandbox_backend': 'linux_bwrap_seccomp_v1',
    }))
    constructed = []

    class Server:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.process = type('Process', (), {'pid': 12345})()

        def start(self):
            return self

        def request(self, method, params):
            return {'thread': {'id': 'thread-existing'}}

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta, 'CodexAppServer', Server)
    loop = persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=session / 'codex_home', executor=object(),
        meta_config={'model': 'gpt-6-astra', 'provider': 'OpenAI'}, sandbox_backend=None,
    )
    loop.tools.finished = True
    with pytest.raises(RuntimeError, match='sandbox_backend'):
        loop.run_experiment('continue')
    assert constructed == []


def test_partial_spawn_failure_closes_rpc_logs(tmp_path):
    class FailingLauncher:
        def spawn(self, *args):
            raise RuntimeError('controlled spawn failed')

    server = CodexAppServer(
        codex_home=tmp_path, workspace=tmp_path, journal=tmp_path / 'rpc',
        executable='/codex', process_launcher=FailingLauncher(),
    )
    with pytest.raises(RuntimeError, match='controlled spawn failed'):
        server.start()
    assert server.process is None
    server.close()
    assert server.stderr.closed is True
    assert server.events.closed is True


def test_model_catalog_exposes_native_apply_patch():
    project=Path(__file__).resolve().parents[1]
    catalog=json.loads((project/'configs/codex-deepseek-v4.1-flash.json').read_text())
    model=next(row for row in catalog['models'] if row['slug']=='DeepSeek-V4.1-Flash')
    assert model['shell_type']=='unified_exec'
    assert model['apply_patch_tool_type']=='freeform'


def test_materialize_copies_full_current_parent(tmp_path):
    project=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(project/'runtime'))
    from rsiflow.task_meta.rsiflow_manifest import load_manifest
    seed=project/'seed_harness/rsiflow_base_manifest.json'
    state=tmp_path/'task.json'
    write_json(state,{'harness_path':str(seed)})
    result=ControllerTools(project,receipt_root=tmp_path/'receipts').execute({
        'operation':'materialize_harness','state_path':str(state),'destination':str(tmp_path/'candidate')})
    assert result['status']=='materialized'
    expected=load_manifest(seed)
    assert set(result['files'])==set(expected.files)
    for name,content in expected.files.items():
        assert (tmp_path/'candidate'/name).read_text()==content
    assert [Path(p).name for p in result['production_templates']] == [
        '01_module_localization.yaml', '02_improvement_directions.yaml', '03_harness_generation.yaml']
    assert all(Path(p).is_file() for p in result['production_templates'])
    assert len(result['next_steps']) == 4
    assert result['max_validation_attempts'] == 3
    assert Path(result['localization_report_path']).parent.is_dir()
    assert Path(result['improvement_direction_path']).parent.is_dir()
