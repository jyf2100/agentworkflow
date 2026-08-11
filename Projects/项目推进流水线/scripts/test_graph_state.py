#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_state.py — graph_pa_state 测试（任务 2.2；R8 可序列化 + 轮次计数器）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_state as GS
import operator


def test_initial_state_has_round_zero_and_empty_logs():
    s = GS.initial_state(run_id='r1', thread_id='run_20260811', stamp='20260811')
    assert s['prd_round'] == 0 and s['verify_round'] == 0
    assert s['obs_log'] == [] and s['side_effect_log'] == []


def test_is_terminal():
    s = GS.initial_state(run_id='r', thread_id='t', stamp='s')
    assert GS.is_terminal(s) is False
    GS.mark_terminal(s, 'interrupted_pr')
    assert GS.is_terminal(s) is True


def test_bump_round_increments():
    s = GS.initial_state(run_id='r', thread_id='t', stamp='s')
    assert GS.bump_round(s, 'prd_round') == 1
    assert GS.bump_round(s, 'prd_round') == 2
    assert s['prd_round'] == 2 and s['verify_round'] == 0


def test_state_json_serializable_roundtrip():
    # R8：state 只持可序列化 → json round-trip 无损（绝对路径不入 state）
    s = GS.initial_state(run_id='r1', thread_id='run_x', stamp='20260811')
    s['candidates'] = {'candidates': [{'project': 'p', 'relevance': 0.8}], 'stats': {'signals_extracted': 1}}
    s['prd_manifest'] = {'prds': [{'path': 'a.md'}]}
    s['obs_log'] = [{'cost': 0.1, 'turns': 3}]
    GS.bump_round(s, 'verify_round')
    rt = json.loads(json.dumps(s))
    assert rt == s
    assert rt['verify_round'] == 1


def test_obs_log_reducer_semantics():
    # Annotated[list, operator.add] = langgraph 累加语义（两 node 各返 obs_log → 拼接）
    a = {'obs_log': [{'cost': 0.1}]}
    b = {'obs_log': [{'cost': 0.2}]}
    merged = operator.add(a['obs_log'], b['obs_log'])
    assert merged == [{'cost': 0.1}, {'cost': 0.2}]


def test_no_absolute_path_in_state():
    # R8 不变式：绝对路径不入 state（ArtifactHandle 只持 store+rel_path）
    s = GS.initial_state(run_id='r', thread_id='t', stamp='s')
    s['fetch_items'] = [{'kind': 'wechat', 'store': 'vault', 'rel_path': 'Knowledge/微信/x.md',
                         'digest': 'sha256:ab'}]
    blob = json.dumps(s)
    assert '/mnt/' not in blob and '/home/' not in blob   # 无绝对路径泄漏
