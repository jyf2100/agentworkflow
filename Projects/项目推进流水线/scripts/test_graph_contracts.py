#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_contracts.py — graph_pa_contracts 的契约测试（langgraph-workflow-upgrade 任务 2.1/7.3）。

覆盖：ArtifactHandle（store/digest OQ3）、NodeInput/NodeOutput（status/error 联动、obs 必吐、
idempotency_key）、resolve_handle fail-closed、obs_from_meta 归约。AAA 结构，紧凑写法有意。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_contracts as C


# ── ArtifactHandle ──────────────────────────────────────────────────
def test_handle_vault_long_term_requires_digest():
    h = C.validate_artifact_handle({'kind': 'prd', 'store': 'vault',
                                    'rel_path': 'a.md', 'digest': 'sha256:abc'})
    assert h['store'] == 'vault'


def test_handle_worktree_requires_digest():
    C.validate_artifact_handle({'kind': 'diff', 'store': 'worktree',
                                'rel_path': 'd.patch', 'digest': 'sha256:de'})


def test_handle_tmp_digest_optional():
    # OQ3：tmp 产物 digest 可选（install_log/test_log 量大，省 sha 成本）
    h = C.validate_artifact_handle({'kind': 'install_log', 'store': 'tmp', 'rel_path': 'x.log'})
    assert 'digest' not in h


def test_handle_tmp_digest_when_present_must_be_sha():
    try:
        C.validate_artifact_handle({'kind': 'x', 'store': 'tmp', 'rel_path': 'y', 'digest': 'md5:zz'})
        assert False, '应 raise'
    except C.ContractError:
        pass


def test_handle_long_term_missing_digest_raises():
    try:
        C.validate_artifact_handle({'kind': 'prd', 'store': 'vault', 'rel_path': 'a.md'})
        assert False, '长期 artifact 缺 digest 应 raise'
    except C.ContractError:
        pass


def test_handle_bad_store_rejected():
    for bad in ('s3', '', None):
        try:
            C.validate_artifact_handle({'kind': 'k', 'store': bad, 'rel_path': 'r'})
            assert False, f'{bad!r} 应被拒'
        except C.ContractError:
            pass


def test_handle_bad_types_rejected():
    for d in [None, 'str', 42, []]:
        try:
            C.validate_artifact_handle(d); assert False
        except C.ContractError:
            pass
    try:
        C.validate_artifact_handle({'kind': '', 'store': 'vault', 'rel_path': 'r', 'digest': 'sha256:x'})
        assert False
    except C.ContractError:
        pass


# ── NodeOutput ──────────────────────────────────────────────────────
def _ok(**kw):
    base = {'status': 'ok', 'obs': {}, 'idempotency_key': 'k'}
    base.update(kw); return base


def test_output_ok_minimal():
    assert C.validate_node_output(_ok())['status'] == 'ok'


def test_output_ok_with_artifacts_validates_each():
    o = _ok(artifacts=[{'kind': 'a', 'store': 'tmp', 'rel_path': 'x'}])
    C.validate_node_output(o)


def test_output_bad_artifact_propagates():
    try:
        C.validate_node_output(_ok(artifacts=[{'kind': 'a', 'store': 'bogus', 'rel_path': 'x'}]))
        assert False
    except C.ContractError:
        pass


def test_output_non_ok_requires_error_with_valid_code():
    for code in C.VALID_ERROR_CODES:
        o = {'status': 'blocked', 'obs': {}, 'idempotency_key': 'k',
             'error': {'code': code, 'message': 'm'}}
        C.validate_node_output(o)


def test_output_non_ok_missing_error_raises():
    try:
        C.validate_node_output({'status': 'blocked', 'obs': {}, 'idempotency_key': 'k'})
        assert False
    except C.ContractError:
        pass


def test_output_bad_error_code_rejected():
    try:
        C.validate_node_output({'status': 'blocked', 'obs': {}, 'idempotency_key': 'k',
                                'error': {'code': 'made_up', 'message': 'm'}})
        assert False
    except C.ContractError:
        pass


def test_output_obs_required():
    try:
        C.validate_node_output({'status': 'ok', 'idempotency_key': 'k'})
        assert False
    except C.ContractError:
        pass


def test_output_idempotency_key_required():
    try:
        C.validate_node_output({'status': 'ok', 'obs': {}})
        assert False
    except C.ContractError:
        pass


def test_output_bad_status_rejected():
    try:
        C.validate_node_output({'status': 'wonderful', 'obs': {}, 'idempotency_key': 'k'})
        assert False
    except C.ContractError:
        pass


def test_output_verdict_struct_validated_when_present():
    # verdict 结构校验（value/reason 必填）；来源约束（仅 PersonaNode）归 check_boundary.py
    C.validate_node_output(_ok(verdict={'value': 'pass', 'reason': 'r'}))
    try:
        C.validate_node_output(_ok(verdict={'value': '', 'reason': 'r'}))
        assert False
    except C.ContractError:
        pass


def test_output_all_statuses_accepted():
    for s in C.VALID_STATUSES:
        if s == C.STATUS_OK:
            C.validate_node_output({'status': s, 'obs': {}, 'idempotency_key': 'k'})
        else:
            C.validate_node_output({'status': s, 'obs': {}, 'idempotency_key': 'k',
                                    'error': {'code': C.ERR_CONTRACT_VIOLATION, 'message': 'm'}})


# ── NodeInput ───────────────────────────────────────────────────────
def test_input_minimal_ok():
    C.validate_node_input({'run_id': 'r1', 'stage': 'radar', 'config': {}})


def test_input_missing_required_raises():
    for d in [{'stage': 'radar', 'config': {}}, {'run_id': 'r', 'config': {}}, {'run_id': 'r', 'stage': 'radar'}]:
        try:
            C.validate_node_input(d); assert False
        except C.ContractError:
            pass


def test_input_upstream_artifacts_validated():
    try:
        C.validate_node_input({'run_id': 'r', 'stage': 's', 'config': {},
                               'upstream_artifacts': [{'kind': 'k', 'store': 'bogus', 'rel_path': 'r'}]})
        assert False
    except C.ContractError:
        pass


# ── resolve_handle（fail-closed）─────────────────────────────────────
def test_resolve_handle_vault():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, 'a.md'), 'w').close()
        p = C.resolve_handle({'store': 'vault', 'rel_path': 'a.md', 'must_exist': True}, vault_root=d)
        assert p.endswith('a.md')


def test_resolve_handle_must_exist_missing_raises():
    with tempfile.TemporaryDirectory() as d:
        try:
            C.resolve_handle({'store': 'tmp', 'rel_path': 'nope.log', 'must_exist': True}, state_dir=d)
            assert False
        except C.MissingArtifactError:
            pass


def test_resolve_handle_missing_root_raises():
    try:
        C.resolve_handle({'store': 'worktree', 'rel_path': 'a'})  # 无 worktree_root
        assert False
    except C.ContractError:
        pass


# ── obs_from_meta ───────────────────────────────────────────────────
def test_obs_from_meta_reduces_model_usage():
    obs = C.obs_from_meta({'cost': 0.2, 'turns': 4, 'duration_ms': 900,
                           'model': {'glm-5.2': {'input': 100, 'output': 50}}})
    assert obs['cost'] == 0.2 and obs['turns'] == 4 and obs['model'] == 'glm-5.2'
    assert obs['token_usage'] == {'input': 100, 'output': 50}


def test_obs_from_meta_none_yields_empty_dict():
    assert C.obs_from_meta(None) == {}
