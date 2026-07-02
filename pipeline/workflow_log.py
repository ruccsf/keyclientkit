"""
工作流日志 — JSON Lines 格式写入 sessions/{client}/workflow.jsonl
========================================================================
用法:
    from workflow_log import log_event

    log_event('北京首农', 'qcc_fetch', 'api_call', tool='get_financial_data', success=True, rows=12)
    log_event('北京首农', 'pdf_extract', 'bs_done', items=61, source='cache')
    log_event('北京首农', 'web_fill', 'batch_done', filled=5, failed=2, failed_fields=['应付债券'])
    log_event('北京首农', 'export', 'audit', result='passed', guards={...})

每次写入一行 JSON，追加模式。同一次运行可连续调用。
"""

import json
import os
from pathlib import Path
from datetime import datetime


# 日志目录：与 data.json 同目录（sessions/{client}/workflow.jsonl）
_LOG_DIR = Path(__file__).parent.parent / 'sessions'


def _get_workflow_env():
    """获取当前 AI 智能体环境标识。"""
    env_bits = []
    for key in ['WORKBUDDY_AGENT', 'WORKBUDDY_PROJECT', 'CLAUDE_CODE_SESSION']:
        val = os.environ.get(key, '')
        if val:
            env_bits.append(f'{key}={val[:40]}')
    return '; '.join(env_bits) if env_bits else 'unknown'


def log_event(client_name: str, step: str, event: str, **details):
    """
    记录一条工作流事件。

    Args:
        client_name: 企业名称（session 目录名）
        step: 步骤标识（qcc_fetch / pdf_extract / web_fill / export）
        event: 事件名（api_call / bs_done / subs_done / batch_done / audit / blocked）
        **details: 事件详情（任意 JSON 可序列化的键值对）
    """
    try:
        session_dir = _LOG_DIR / client_name
        session_dir.mkdir(parents=True, exist_ok=True)

        entry = {
            'ts': datetime.now().isoformat(timespec='seconds'),
            'env': _get_workflow_env(),
            'step': step,
            'event': event,
            **details,
        }

        log_path = session_dir / 'workflow.jsonl'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    except Exception:
        pass  # 日志失败不影响主流程


def read_log(client_name: str, tail: int = 50) -> list[dict]:
    """读取最近 N 条日志。"""
    log_path = _LOG_DIR / client_name / 'workflow.jsonl'
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return entries[-tail:] if tail > 0 else entries
