"""Single-use verification: temporary Git fixtures and fake commands only."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from prax import repository_harness as harness
from prax.harness_contract import control_fingerprint, load_contract, snapshot
from prax.verification_runtime import validate_report

VERIFIER = '''import hashlib, json, os, pathlib, sys, time
root = pathlib.Path.cwd()
directory = pathlib.Path(os.environ['PRAX_VERIFICATION_DIR'])
with (directory / 'dispatches.txt').open('a') as stream:
    stream.write('once\\n')
assert os.environ['PRAX_VERIFICATION_AUTHORIZED'] == '1'
assert not any(key.startswith('E2E_') for key in os.environ)
mode = sys.argv[1]
if mode == 'timeout':
    time.sleep(30)
if mode == 'missing':
    raise SystemExit(0)
status = mode if mode in ('passed', 'failed', 'blocked') else 'passed'
artifact = directory / 'proof.txt'
artifact.write_text('selected test evidence')
report = {'schemaVersion': 1, 'run_id': os.environ['PRAX_RUN_ID'],
          'execution_id': os.environ['PRAX_EXECUTION_ID'], 'status': status,
          'tests': [] if status == 'blocked' else [{'id': 'selected-1', 'status': status}],
          'coverage': {'status': 'unknown', 'reason': 'Selected cases only'},
          'evidence': [{'path': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}],
          'context': {'platform': 'fixture', 'mode': 'acceptance-only'}}
if mode == 'zero':
    report['tests'] = []
if mode == 'stale':
    report['execution_id'] = 'old-execution'
if mode == 'drift':
    (root / 'source.txt').write_text('changed')
if mode == 'control-drift':
    (root / '.prax/harness.json').write_text('{}')
pathlib.Path(os.environ['PRAX_VERIFICATION_REPORT']).write_text(json.dumps(report))
raise SystemExit(7 if mode == 'bad-exit' else {'passed': 0, 'failed': 1, 'blocked': 2}[status])
'''

ADAPTER = '''import os, pathlib, sys
directory = pathlib.Path(os.environ['PRAX_VERIFICATION_DIR'])
phase = sys.argv[sys.argv.index('--phase') + 1]
assert phase in ('preflight', 'check'), phase
with (directory / 'phases.txt').open('a') as stream:
    stream.write(phase + '\\n')
'''


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / '.prax').mkdir()
    (root / 'source.txt').write_text('unchanged')
    (root / 'verify.py').write_text(VERIFIER)
    (root / 'adapter.py').write_text(ADAPTER)
    (root / '.gitignore').write_text('.prax/harness-runs/\n__pycache__/\n')
    profile = {'schemaVersion': 1, 'adapter': {'argv': [sys.executable, '-B', 'adapter.py']}}
    harness.write_json(root / '.prax/harness.json', profile)
    for argv in (["git", "init", "-b", "dev"], ["git", "add", "."],
                 ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture"]):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    task = {'schemaVersion': 1, 'id': 'verify-once', 'kind': 'verification', 'goal': 'Check selected tests',
            'repositories': [{'path': '.', 'branch': 'dev', 'scope': []}],
            'budget': {'wall_seconds': 30},
            'verifier': {'argv': [sys.executable, '-B', 'verify.py', 'passed'], 'cwd': '.',
                         'timeout_seconds': 5, 'report_schema': 'prax-verification-v1'}}
    return SimpleNamespace(root=root, task=task, profile=profile,
                           run_dir=root / '.prax/harness-runs/verify-once')


def run(repo, mode='passed'):
    repo.task['verifier']['argv'][-1] = mode
    contract = load_contract(repo.root, repo.task, repo.profile)
    return harness.execute(repo.root, repo.run_dir, contract, authorize_verification=True,
                           executor=Mock(side_effect=AssertionError('no worker permitted')))


@pytest.mark.parametrize(('mode', 'status', 'verified'), [
    ('passed', 'PASS', True), ('failed', 'FEATURE-FAIL', False), ('blocked', 'BLOCKED', False),
    ('zero', 'HARNESS-ERROR', False), ('missing', 'HARNESS-ERROR', False),
    ('stale', 'HARNESS-ERROR', False), ('bad-exit', 'HARNESS-ERROR', False),
    ('drift', 'HARNESS-ERROR', False), ('control-drift', 'HARNESS-ERROR', False),
])
def test_single_dispatch_report_modes(repository, monkeypatch, mode, status, verified):
    monkeypatch.setenv('E2E_OLD_AUTH', 'must-not-leak')
    monkeypatch.setattr(harness, 'OrchestratorLoop', Mock(side_effect=AssertionError('no loop permitted')))
    result = run(repository, mode)
    assert (result['status'], result['verified']) == (status, verified)
    assert result['iterations'] == 0 and result['worker'] is None
    assert result['verification_dispatches'] == 1
    assert result['retry_allowed'] is False and result['resume_allowed'] is False
    assert 'platform_validation' not in result
    evidence_dir = Path(result['report_path']).parent
    assert (evidence_dir / 'dispatches.txt').read_text() == 'once\n'
    phases = (evidence_dir / 'phases.txt').read_text().splitlines()
    assert phases[:2] == ['preflight', 'check'] and set(phases) <= {'preflight', 'check'}
    assert harness.read_json(repository.run_dir / 'result.json') == result
    if verified:
        assert result['summary'] == {'total': 1, 'completed': 1, 'passed': 1, 'failed': 0,
                                     'blocked': 0, 'not_executed': 0}
        assert result['context']['platform'] == 'fixture'
    with pytest.raises(ValueError, match='already dispatched'):
        run(repository, mode)


def test_timeout_is_not_retried(repository):
    repository.task['verifier']['timeout_seconds'] = 1
    result = run(repository, 'timeout')
    assert result['status'] == 'HARNESS-ERROR' and not result['verified']
    assert 'timed out' in result['error']
    assert result['cleanup_budget_seconds'] == 12
    assert result['external_process_stop'] == 'unconfirmed'
    assert (Path(result['report_path']).parent / 'dispatches.txt').read_text() == 'once\n'
    with pytest.raises(ValueError, match='already dispatched'):
        run(repository, 'timeout')


def test_failed_preflight_reserves_attempt_without_dispatching_verifier(repository, monkeypatch):
    monkeypatch.setattr(harness, 'adapter', Mock(side_effect=harness.BoundaryStop('authorization denied')))
    result = run(repository)
    assert result['status'] == 'HARNESS-ERROR' and not result['verified']
    assert result['attempt_reserved'] is True and result['verification_dispatches'] == 0
    assert not (Path(result['report_path']).parent / 'dispatches.txt').exists()
    with pytest.raises(ValueError, match='already dispatched'):
        run(repository)


def test_adapter_cannot_prepopulate_acceptance_report(repository, monkeypatch):
    original = harness.adapter
    def prepopulate(*args, **kwargs):
        original(*args, **kwargs)
        Path(kwargs['env']['PRAX_VERIFICATION_REPORT']).write_text('{}')
    monkeypatch.setattr(harness, 'adapter', prepopulate)
    result = run(repository)
    assert result['status'] == 'HARNESS-ERROR' and result['verification_dispatches'] == 0
    assert 'before verifier dispatch' in result['error']


def test_plan_needs_no_authorization_and_run_needs_explicit_flag(repository, monkeypatch, capsys):
    task_path = repository.root.parent / 'task.json'
    task_path.write_text(json.dumps(repository.task))
    args = ['--root', str(repository.root), '--task', str(task_path)]
    assert harness.main(['plan', *args]) == 0
    assert json.loads(capsys.readouterr().out)['execution'] == 'not-started'
    assert not repository.run_dir.exists()
    supervisor = Mock(return_value={'status': 'PASS', 'verified': True})
    monkeypatch.setattr(harness, 'supervise', supervisor)
    assert harness.main(['run', *args]) == 2
    assert 'authorize-verification' in capsys.readouterr().err
    supervisor.assert_not_called()
    assert harness.main(['run', *args, '--authorize-verification']) == 0
    assert supervisor.call_args.kwargs['authorize_verification'] is True


@pytest.mark.parametrize('options', [{}, {'resume_existing': True}, {'explicit_resume': True},
                                    {'resume_existing': True, 'authorize_verification': True},
                                    {'explicit_resume': True, 'authorize_verification': True}])
def test_run_requires_current_authorization_and_rejects_all_resume(repository, monkeypatch, options):
    supervisor = Mock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(harness, 'supervise', supervisor)
    contract = load_contract(repository.root, repository.task, repository.profile)
    with pytest.raises(ValueError, match='authorization|authorize-verification|cannot resume'):
        harness.run_contract(repository.root, contract, **options)
    assert not repository.run_dir.exists()
    supervisor.assert_not_called()


def test_partial_crash_marker_blocks_even_without_result(repository):
    repository.run_dir.mkdir(parents=True)
    (repository.run_dir / 'verification-dispatch.json').write_text('{')
    contract = load_contract(repository.root, repository.task, repository.profile)
    with pytest.raises(ValueError, match='already dispatched'):
        harness.run_contract(repository.root, contract, authorize_verification=True)
    with pytest.raises(ValueError, match='already dispatched'):
        harness.execute(repository.root, repository.run_dir, contract, authorize_verification=True)


def test_real_supervisor_pass_is_single_use(repository):
    contract = load_contract(repository.root, repository.task, repository.profile)
    result = harness.run_contract(repository.root, contract, authorize_verification=True)
    assert result['status'] == 'PASS', result
    assert result['verified'] is True
    assert result['execution_id'] == result['verification_report']['execution_id']
    for options in ({'authorize_verification': True},
                    {'authorize_verification': True, 'resume_existing': True}):
        with pytest.raises(ValueError, match='already dispatched|cannot resume'):
            harness.run_contract(repository.root, contract, **options)


def test_supervisor_timeout_preserves_uncertain_stop_and_never_cached_success(repository, monkeypatch):
    repository.run_dir.mkdir(parents=True)
    harness.write_json(repository.run_dir / 'result.json', {'status': 'PASS', 'verified': True})
    contract = load_contract(repository.root, repository.task, repository.profile)
    proc = Mock(pid=987654321)
    proc.wait.side_effect = subprocess.TimeoutExpired('runtime', 30)
    cleanup = Mock()
    monkeypatch.setattr(harness.subprocess, 'Popen', Mock(return_value=proc))
    monkeypatch.setattr(harness, 'wait_for_runtime',
                        Mock(side_effect=subprocess.TimeoutExpired('runtime', 30)))
    monkeypatch.setattr(harness, 'terminate_group', cleanup)
    result = harness.supervise(repository.root, repository.run_dir, contract,
                               watch_pause=True, authorize_verification=True)
    assert result['status'] == 'HARNESS-ERROR' and not result['verified']
    assert result['external_process_stop'] == 'unconfirmed'
    assert result['retry_allowed'] is False and result['resume_allowed'] is False
    cleanup.assert_called_once_with(proc, grace_seconds=12)


def test_verification_pause_is_blocked_not_resumable(repository, monkeypatch):
    repository.run_dir.mkdir(parents=True)
    contract = load_contract(repository.root, repository.task, repository.profile)
    before = snapshot(repository.root, contract['task'])
    harness.write_json(repository.run_dir / 'identity.json', {
        'fingerprint': control_fingerprint(repository.root, contract),
        'snapshot': before,
    })
    proc = Mock(pid=987654321)
    monkeypatch.setattr(harness, 'snapshot', Mock(return_value=before))
    monkeypatch.setattr(harness.subprocess, 'Popen', Mock(return_value=proc))
    monkeypatch.setattr(harness, 'wait_for_runtime', Mock(side_effect=harness.PauseRequested('paused')))
    monkeypatch.setattr(harness, 'terminate_group', Mock())
    result = harness.supervise(repository.root, repository.run_dir, contract,
                               watch_pause=True, authorize_verification=True)
    assert result['status'] == 'BLOCKED', result
    assert result['stop_reason'] == 'paused' and result['resume_allowed'] is False
    assert result['external_process_stop'] == 'unconfirmed'


def test_core_keyboard_interrupt_preserves_uncertain_stop(repository, monkeypatch):
    monkeypatch.setattr(harness, 'adapter', Mock(side_effect=KeyboardInterrupt()))
    result = run(repository)
    assert result['status'] == 'HARNESS-ERROR' and not result['verified']
    assert result['stop_reason'] == 'verification_interrupted'
    assert result['external_process_stop'] == 'unconfirmed'
    assert result['verification_dispatches'] == 0 and result['attempt_reserved'] is True


def test_contract_verification_is_empty_scope_zero_repairs_and_requires_adapter(repository):
    normalized = load_contract(repository.root, repository.task, repository.profile)
    assert normalized['task']['budget'] == {'wall_seconds': 30, 'max_iterations': 0, 'stuck_after': 0}
    for mutation in ('scope', 'adapter', 'schema', 'iterations', 'stuck'):
        task, profile = copy.deepcopy(repository.task), copy.deepcopy(repository.profile)
        if mutation == 'scope': task['repositories'][0]['scope'] = ['.']
        if mutation == 'adapter': profile.pop('adapter')
        if mutation == 'schema': task['verifier'].pop('report_schema')
        if mutation == 'iterations': task['budget']['max_iterations'] = 1
        if mutation == 'stuck': task['budget']['stuck_after'] = True
        with pytest.raises(ValueError):
            load_contract(repository.root, task, profile)


@pytest.fixture
def report_fixture(tmp_path):
    directory = tmp_path.resolve() / 'evidence'
    directory.mkdir()
    artifact = directory / 'artifact.txt'
    artifact.write_text('proof')
    report = {'schemaVersion': 1, 'run_id': 'run', 'execution_id': 'execution', 'status': 'passed',
              'tests': [{'id': 'test-1', 'status': 'passed'}],
              'coverage': {'status': 'unknown', 'reason': 'Selection does not prove full coverage'},
              'context': {}, 'evidence': [{'path': str(artifact),
                                         'sha256': hashlib.sha256(b'proof').hexdigest()}]}
    return directory, artifact, report


@pytest.mark.parametrize('mutation', ['empty', 'skipped', 'duplicate', 'blocked-in-failed', 'no-failure',
                                    'hash', 'missing-file', 'escape', 'symlink', 'no-evidence',
                                    'wrong-run', 'wrong-execution', 'coverage', 'schema-bool',
                                    'extra-field', 'invalid-context', 'fake-exit-zero'])
def test_report_fails_closed(report_fixture, mutation):
    directory, artifact, report = report_fixture
    code = 0
    if mutation == 'empty': report['tests'] = []
    if mutation == 'skipped': report['tests'][0]['status'] = 'not-executed'
    if mutation == 'duplicate': report['tests'] *= 2
    if mutation == 'blocked-in-failed':
        report.update(status='failed', tests=[{'id': 'a', 'status': 'failed'}, {'id': 'b', 'status': 'blocked'}])
        code = 1
    if mutation == 'no-failure': report['status'], code = 'failed', 1
    if mutation == 'hash': report['evidence'][0]['sha256'] = '0' * 64
    if mutation == 'missing-file': artifact.unlink()
    if mutation == 'escape':
        outside = directory.parent / 'outside.txt'
        outside.write_text('proof')
        report['evidence'][0]['path'] = str(outside)
    if mutation == 'symlink':
        link = directory / 'link.txt'
        link.symlink_to(artifact)
        report['evidence'][0]['path'] = str(link)
    if mutation == 'no-evidence': report['evidence'] = []
    if mutation == 'wrong-run': report['run_id'] = 'previous'
    if mutation == 'wrong-execution': report['execution_id'] = 'previous'
    if mutation == 'coverage': report['coverage']['status'] = 'complete'
    if mutation == 'schema-bool': report['schemaVersion'] = True
    if mutation == 'extra-field': report['implicit_success'] = True
    if mutation == 'invalid-context': report['context'] = []
    if mutation == 'fake-exit-zero':
        report.update(status='failed', tests=[{'id': 'a', 'status': 'failed'}])
    path = directory / 'report.json'
    path.write_text(json.dumps(report))
    with pytest.raises((ValueError, OSError)):
        validate_report(path, directory=directory, run_id='run', execution_id='execution', returncode=code)


def test_blocked_without_tests_never_passes(report_fixture):
    directory, _, report = report_fixture
    report.update(status='blocked', tests=[], evidence=[])
    path = directory / 'report.json'
    path.write_text(json.dumps(report))
    raw, summary = validate_report(path, directory=directory, run_id='run', execution_id='execution', returncode=2)
    assert raw['status'] == 'blocked' and summary['completed'] == 0
