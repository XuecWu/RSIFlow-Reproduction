"""Native BFCL v3 / ACE scoring, fixed IDs and isolated tool execution."""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from common import ROOT, read, write, sha, immutable
from harness_api import HarnessAPI
from tool_sandbox import run_native
from rsiflow.task_meta.durable import value_hash


def validate_tool_selection(identifier, frozen, tasks):
    """Validate the bounded tool queue without widening the daily 50-task contract."""
    count = len(tasks)
    if frozen.get('source_role') != 'independent_validation' or frozen.get('explicit_full_benchmark'):
        return count
    if count == 50:
        return count
    scope = frozen.get('evaluation_scope')
    if (identifier != 'bfcl_v3' or frozen.get('purpose') != 'report_only'
            or frozen.get('formal_report_evaluation') is not True
            or not frozen.get('manifest_hash')
            or not isinstance(scope, dict)
            or scope.get('manifest_hash') != frozen.get('manifest_hash')
            or scope.get('source_role') != frozen.get('source_role')
            or scope.get('system_snapshot_sha256') != frozen.get('system_snapshot_sha256')
            or scope.get('evaluation_config_sha256') != frozen.get('evaluation_config_sha256')
            or not all(scope.get(key) for key in ('manifest_hash', 'system_snapshot_sha256', 'evaluation_config_sha256'))):
        raise ValueError('Independent tool evaluation requires exactly 50 frozen tasks')
    if count != 152:
        raise ValueError('Formal BFCL report evaluation requires exactly 152 frozen tasks')
    if len({task.get('task_id') for task in tasks}) != count:
        raise ValueError('Formal BFCL report evaluation task IDs must be unique')
    return count


def attribution_from_receipts(receipts, task_receipts, state_hash, selected_ids, selected_categories):
    """Build attribution only from explicit native context carried by trusted receipts."""
    selected_ids = sorted(str(item) for item in selected_ids)
    if (not selected_ids or len(set(selected_ids)) != len(selected_ids)
            or not isinstance(task_receipts, dict) or not isinstance(selected_categories, dict)):
        raise RuntimeError('BFCL attribution requires explicit native task context')
    if set(task_receipts) != set(selected_ids) or set(selected_categories) != set(selected_ids):
        raise RuntimeError('BFCL attribution native task context does not cover the selected IDs')
    if not isinstance(receipts, dict) or any(not isinstance(key, str) or not key for key in receipts):
        raise RuntimeError('BFCL attribution transport receipt set is invalid')
    owner = {}
    result = {}
    for native_id, keys in task_receipts.items():
        if not isinstance(keys, list) or not keys:
            raise RuntimeError('BFCL attribution is missing a task receipt')
        result[native_id] = []
        for key in keys:
            if key in owner:
                raise RuntimeError('BFCL attribution receipt is shared by multiple tasks')
            receipt = receipts.get(key)
            if not isinstance(receipt, dict) or receipt.get('status') != 'completed':
                raise RuntimeError('BFCL attribution receipt is not completed')
            identity = receipt.get('identity') or {}
            calls = receipt.get('calls')
            if (receipt.get('user_simulator') is not False or not isinstance(calls, list) or not calls
                    or any(not isinstance(call, dict) or call.get('status') != 'completed' for call in calls)):
                raise RuntimeError('BFCL attribution receipt has no completed local model calls')
            if identity.get('state_hash') != state_hash or not isinstance(identity.get('request'), dict):
                raise RuntimeError('BFCL attribution receipt binding is invalid')
            context = identity.get('attribution') or {}
            if context.get('native_id') != native_id or context.get('category') != selected_categories[native_id]:
                raise RuntimeError('BFCL attribution native context is missing or mismatched')
            owner[key] = native_id
            result[native_id].append({'receipt_key': key, 'request_sha256': value_hash(identity['request'])})
    if set(owner) != set(receipts):
        raise RuntimeError('BFCL attribution contains receipts without explicit native task context')
    return {'selected_task_ids': selected_ids, 'selected_task_ids_sha256': value_hash(selected_ids),
            'selected_task_categories': selected_categories,
            'state_hash': state_hash, 'tasks': result,
            'receipt_keys_sha256': value_hash(sorted(owner))}


def validate_call_sidecars(receipts, sidecars):
    """Reconcile HarnessAPI per-call files against their primary transport receipts."""
    by_receipt = {}
    for name, sidecar in sidecars.items():
        if '.call_' not in name:
            raise RuntimeError('Invalid local model-call sidecar name')
        receipt_key, index = name.rsplit('.call_', 1)
        if not index.endswith('.json'):
            raise RuntimeError('Invalid local model-call sidecar name')
        index = index[:-5]
        if not index.isdigit() or receipt_key not in receipts:
            raise RuntimeError('Local model-call sidecar has no primary receipt')
        if not isinstance(sidecar, dict) or sidecar.get('status') != 'completed':
            raise RuntimeError('Local model-call sidecar is not completed')
        slot = by_receipt.setdefault(receipt_key, set())
        if index in slot:
            raise RuntimeError('Duplicate local model-call sidecar index')
        slot.add(index)
    for key, receipt in receipts.items():
        if receipt.get('status') != 'completed':
            raise RuntimeError('Transport receipt is not completed: ' + key)
        if receipt.get('user_simulator') is True:
            if by_receipt.get(key):
                raise RuntimeError('User-simulator receipt unexpectedly owns local model-call sidecars')
            continue
        calls = receipt.get('calls')
        if (not isinstance(calls, list) or not calls
                or any(not isinstance(call, dict) or call.get('status') != 'completed' for call in calls)):
            raise RuntimeError('Primary receipt has incomplete local model calls')
        indexes = by_receipt.get(key, set())
        expected = {f'{index:03d}' for index in range(len(calls))}
        if indexes != expected:
            raise RuntimeError('Local model-call sidecars do not match primary receipt calls')
    return sum(len(receipt.get('calls', [])) for receipt in receipts.values()
               if receipt.get('status') == 'completed' and receipt.get('user_simulator') is False)


def transport_evidence(directory):
    paths = list(Path(directory).glob('*.json'))
    receipts = {path.stem: read(path) for path in paths if '.call_' not in path.name}
    sidecars = {path.name: read(path) for path in paths if '.call_' in path.name}
    calls = validate_call_sidecars(receipts, sidecars)
    return receipts, sidecars, calls

def records(path):
    text=Path(path).read_text(encoding='utf-8')
    try:value=json.loads(text)
    except json.JSONDecodeError:return [json.loads(line) for line in text.splitlines() if line.strip()]
    return value if isinstance(value,list) else [value]

def native_layout(identifier,spec):
    repo=Path(spec['repository_root']).resolve()
    if identifier=='bfcl_v3':
        matches=list(repo.glob('**/bfcl_eval/constants/model_config.py'))
        if len(matches)!=1:raise ValueError('BFCL package root is ambiguous or missing')
        repo=matches[0].parents[2]
        paths=sorted((repo/'bfcl_eval/data').glob('BFCL_v3_*.json'))
        if not paths:raise ValueError('Pinned BFCL-v3 data missing; V4 substitution is forbidden')
        categories=[p.stem.removeprefix('BFCL_v3_') for p in paths]
        spec={**spec,'categories':categories}
    else:
        paths=sorted((repo/'data_all'/('data_'+spec['language'])).glob('data_*.json'))
        if not paths:raise ValueError('ACE native language data missing')
    ids={}
    for p in paths:
        rows=records(p)
        if not rows or not all(isinstance(r,dict) and 'id' in r for r in rows):raise ValueError('Unsupported native data file: '+str(p))
        category=p.stem.removeprefix('BFCL_v3_').removeprefix('data_')
        ids[category]=[str(r['id']) for r in rows]
        if len(ids[category])!=len(set(ids[category])):raise ValueError('Duplicate native benchmark ID')
    return repo,spec,ids,paths

def check_native(identifier,spec,output):
    """Import/register the actual official runners in isolation; zero inference."""
    output=Path(output).absolute();output.mkdir(parents=True,exist_ok=True)
    repo,spec,ids,files=native_layout(identifier,spec)
    work=output/'native_work'
    if not work.exists():shutil.copytree(repo,work,ignore=shutil.ignore_patterns('.git','__pycache__','.env*','result*','score*'))
    write(output/'spec.json',spec)
    command=[str(spec.get('python_executable',ROOT/'.venv/bin/python')),'-B',str(ROOT/'official_tool_entry.py'),identifier,'check','gpt-rsi-preflight',str(work),str(output),str(output/'spec.json')]
    calls=[]
    def fake_only(body):
        if body['model']!='gpt-rsi-preflight' or len(calls)>=2:raise ValueError('Unexpected preflight RPC')
        calls.append(body)
        return {'id':'test_override','object':'chat.completion','created':0,'model':body['model'],
                'choices':[{'index':0,'message':{'role':'assistant','content':'test_override_cpu_only'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
    with (output/'check.log').open('wb') as log:
        run_native(command,work,output,log,handler=fake_only,timeout=900)
    if len(calls)!=2:raise RuntimeError('Official SDK bridge did not complete the CPU test')
    write(output/'check.json',{'status':'completed','controlled_rpc_calls':2,
        'response':'test_override_cpu_only','requests':calls})

def metric_rows(identifier, output, ids):
    summaries=[];task_results=[]
    for category,expected in ids.items():
        stem=('BFCL_v3_' if identifier=='bfcl_v3' else 'data_')+category
        preds=list(output.glob('**/'+stem+'_result.json'))
        scores=list(output.glob('**/'+stem+'_score.json'))
        if len(preds)!=1 or len(scores)!=1:raise RuntimeError('Missing/ambiguous native category: '+category)
        predictions=records(preds[0]);actual=[str(r['id']) for r in predictions]
        if len(actual)!=len(set(actual)) or set(actual)!=set(expected):raise RuntimeError('Incomplete official denominator: '+category)
        scored=records(scores[0]);first=scored[0]
        metric=first.get('accuracy',first.get('end_to_end_accuracy'))
        if type(metric) not in (int,float) or not math.isfinite(metric) or not 0<=metric<=1:
            raise RuntimeError('Invalid official metric: '+category)
        count=first.get('total_count',first.get('total'))
        if count!=len(expected):raise RuntimeError('Official score denominator mismatch: '+category)
        failures={}
        for row in scored[1:]:
            ident=row.get('id')
            if identifier=='acebench' and category.startswith('agent_') and type(ident) is int:
                if not 0<=ident<len(actual):raise RuntimeError('Invalid native ACE failure index')
                ident=actual[ident]
            ident=str(ident)
            if ident not in actual or row.get('valid') is not False:
                raise RuntimeError('Unsupported official per-task score format')
            failures[ident]=row
        if first.get('correct_count')!=len(expected)-len(failures):
            raise RuntimeError('Official success count does not reconcile with task-level failures')
        for row in predictions:
            ident=str(row['id']);error=failures.get(ident,{})
            task_results.append(dict(native_id=ident,success=ident not in failures,scoring_status='completed',
                execution_status='completed',native_scores={'official_task_pass':int(ident not in failures)},
                failure_type=error.get('error_type') or ('task_failure' if error else None),
                tokens={'input':row.get('input_token_count'),'output':row.get('output_token_count')},
                tool_calls=None,latency=row.get('latency'),trajectory_path=str(preds[0]),category=category))
        summaries.append({'category':category,'accuracy':metric,'denominator':count,
                          'score_sha256':sha(scores[0]),'predictions_sha256':sha(preds[0])})
    total=sum(x['denominator'] for x in summaries)
    return {'status':'completed','expected':total,'completed':total,'categories':summaries,'task_results':task_results,
        'metrics':{'category_macro_accuracy':sum(x['accuracy'] for x in summaries)/len(summaries),
                   'sample_weighted_accuracy':sum(x['accuracy']*x['denominator'] for x in summaries)/total},
        'aggregation_note':'Derived named aggregates; original official category scores and CSVs are retained. No cross-benchmark overall.'}

def evaluate_tool(identifier, config, frozen, spec, user_spec, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    repo,spec,ids,files=native_layout(identifier,spec)
    selected=spec.get('selected_task_ids')
    if frozen.get('source_role')=='independent_validation' and not frozen.get('explicit_full_benchmark'):
        tasks=spec.get('selected_tasks',[])
        validate_tool_selection(identifier, frozen, tasks)
        if len(tasks)!=len({t['task_id'] for t in tasks}) or selected is None:
            raise ValueError('Daily tool runner requires unique frozen tasks and selected IDs')
        if identifier == 'bfcl_v3' and len(tasks) == 152:
            frozen_ids = {str(task['task_id']).split(':', 1)[1] if str(task['task_id']).startswith('bfcl_v3:')
                          else str(task['task_id']) for task in tasks}
            if set(selected) != frozen_ids:
                raise ValueError('Formal BFCL selected IDs differ from the frozen task queue')
    if selected is None and not spec.get('explicit_full_benchmark'):
        raise ValueError('Full tool benchmark requires an explicit separate request')
    if selected is not None:
        if not selected or len(selected)!=len(set(selected)) or set(selected)-{i for v in ids.values() for i in v}:
            raise ValueError('Invalid fixed tool evaluation subset')
        ids={k:[i for i in v if i in set(selected)] for k,v in ids.items()}
        ids={k:v for k,v in ids.items() if v}
        spec={**spec,'categories':list(ids)}
        if identifier == 'bfcl_v3':
            spec['selected_task_categories']={native_id:category for category, values in ids.items() for native_id in values}
        if identifier=='acebench':
            for category,all_ids in native_layout(identifier,spec)[2].items():
                if category.startswith('normal_multi_turn_'):
                    families={i.rsplit('_',1)[0] for i in ids.get(category,[])}
                    required={i for i in all_ids if i.rsplit('_',1)[0] in families}
                    if required!=set(ids.get(category,[])):raise ValueError('ACE subset splits a complete conversation')
    source_hashes={str(p):sha(p) for p in [*repo.rglob('*.py'),*repo.rglob('*.json')] if '__pycache__' not in p.parts and '.git' not in p.parts}
    binding={'state_hash':frozen['state_hash'],'native_data_ids':ids,'source_hashes':source_hashes,
             'spec':spec,'bridge':'official_outer_saved_harness_inner_v1',
             'evaluation_scope':{k:frozen.get(k) for k in ('manifest_hash','source_role','system_snapshot_sha256','evaluation_config_sha256')}}
    immutable(output/'protocol.json',binding)
    if (output/'result.json').exists():
        previous=read(output/'result.json')
        if previous['status']=='completed':
            if previous.get('state_hash') != frozen.get('state_hash'):
                raise RuntimeError('Completed tool cache state binding is invalid')
            recomputed = metric_rows(identifier, output, ids)
            if (previous.get('benchmark') != identifier
                    or any(previous.get(key) != recomputed.get(key)
                           for key in ('expected', 'completed', 'categories', 'metrics', 'task_results'))):
                raise RuntimeError('Completed tool cache does not match its official native outputs')
            receipts, sidecars, verified_calls = transport_evidence(output/'transport')
            if identifier == 'bfcl_v3' and selected is not None:
                binding = previous.get('task_call_attribution')
                if (not isinstance(binding, dict)
                        or binding.get('generator') != 'evaluate_tool_native_request_attribution_v1'
                        or not binding.get('path') or not binding.get('sha256')):
                    raise RuntimeError('Completed BFCL cache lacks trusted task attribution')
                attribution_path = Path(binding['path']).resolve()
                if (not attribution_path.is_relative_to(output.resolve()) or not attribution_path.is_file()
                        or sha(attribution_path) != binding['sha256']):
                    raise RuntimeError('Completed BFCL cache attribution binding is invalid')
                attribution = read(attribution_path)
                if (attribution.get('state_hash') != frozen.get('state_hash')
                        or attribution.get('selected_task_ids_sha256') != value_hash(sorted(selected))
                        or set(attribution.get('selected_task_ids', [])) != set(selected)
                        or attribution.get('generator') != binding.get('generator')):
                    raise RuntimeError('Completed BFCL cache attribution scope is invalid')
                expected_attribution = attribution_from_receipts(
                    receipts,
                    {native_id: [item['receipt_key'] for item in values]
                     for native_id, values in attribution.get('tasks', {}).items()},
                    frozen['state_hash'], sorted(selected), spec['selected_task_categories'])
                if any(attribution.get(key) != value for key, value in expected_attribution.items()):
                    raise RuntimeError('Completed BFCL cache attribution receipt evidence is invalid')
                if previous.get('completed_model_calls') != sum(
                        len(receipts[item['receipt_key']]['calls'])
                        for values in attribution['tasks'].values() for item in values):
                    raise RuntimeError('Completed BFCL cache model-call count is invalid')
            return previous
    # ACE uses relative paths and writes dialogue files; copy public code/data to its own sandbox scratch.
    work=output/'native_work'
    if not work.exists():shutil.copytree(repo,work,ignore=shutil.ignore_patterns('.git','__pycache__','.env*','result*','score*'))
    if selected is not None:
        for path in files:
            # Official ACE only filters its answers when prompt length exceeds result length.
            # Filter BOTH scratch prompts and same-named answer files; comparator stays unchanged.
            for target in work.rglob(path.name):
                raw=records(target)
                if not all(isinstance(r,dict) and 'id' in r for r in raw):continue
                rows=[r for r in raw if str(r['id']) in set(selected)]
                text=target.read_text(encoding='utf-8').lstrip()
                content=json.dumps(rows,ensure_ascii=False) if text.startswith('[') else ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows)
                target.write_text(content,encoding='utf-8')
        queued={str(r['id']) for p in files for r in records(work/p.relative_to(repo))}
        if queued!=set(selected):raise ValueError('Actual native runner queue differs from frozen IDs')
        immutable(output/'task_queue.json',dict(native_ids=sorted(queued),
            complete_tasks=len(spec.get('selected_tasks',selected)),phase='independent_eval',
            global_queue=True,gpu_shards=4))
    api=HarnessAPI(config,frozen,output/'transport',user_spec)
    alias='gpt-rsi-'+frozen['state_hash'][:16]
    write(output/'spec.json',spec)
    try:
        for phase in ['generate','evaluate']:
            marker=output/(phase+'.completed.json')
            if marker.exists():continue
            command=[str(spec.get('python_executable',ROOT/'.venv/bin/python')),'-B',str(ROOT/'official_tool_entry.py'),identifier,phase,alias,str(work),str(output),str(output/'spec.json')]
            with (output/(phase+'.log')).open('ab') as log:
                run_native(command,work,output,log,handler=api.complete)
            pending=[p for p in (output/'transport').glob('*.json') if '.call_' not in p.name and read(p).get('status')!='completed']
            if pending:raise RuntimeError('Benchmark inference has unreconciled calls; scores cannot be accepted')
            write(marker,{'status':'completed','state_hash':frozen['state_hash']})
        result={**metric_rows(identifier,output,ids),'benchmark':identifier,'state_hash':frozen['state_hash'],
                'feedback_to_meta':False,'harness_graph_executed':True}
        receipts, sidecars, verified_calls = transport_evidence(output/'transport')
        if identifier == 'bfcl_v3' and selected is not None:
            task_receipts = {}
            for key, receipt in receipts.items():
                native_id = (receipt.get('identity') or {}).get('attribution', {}).get('native_id')
                if native_id is not None:
                    task_receipts.setdefault(str(native_id), []).append(key)
            selected_ids = sorted(selected)
            attribution = attribution_from_receipts(receipts, task_receipts, frozen['state_hash'], selected_ids,
                                                    spec['selected_task_categories'])
            attribution.update(generator='evaluate_tool_native_request_attribution_v1',
                               benchmark=identifier, official_commit=spec.get('official_commit'),
                               no_order_matching=True)
            attribution_path = output/'task_call_attribution.json'
            immutable(attribution_path, attribution)
            result['task_call_attribution'] = {
                'generator': attribution['generator'], 'path': str(attribution_path),
                'sha256': sha(attribution_path)}
            result['completed_model_calls'] = sum(
                len(receipts[item['receipt_key']]['calls'])
                for values in attribution['tasks'].values() for item in values)
        write(output/'result.json',result);return result
    finally:
        if api.user_client is not None:api.user_client.close()
