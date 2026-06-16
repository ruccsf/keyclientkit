"""
企查查 MCP 调用客户端
====================
直接通过 HTTP 调用企查查 MCP 端点，使用 OAuth 2.0 Bearer Token 认证。
不再依赖 mcporter（Node.js CLI），纯 Python 实现。

前置条件:
  python oauth_qcc.py auth   # 首次使用完成 OAuth 授权（一键命令）

用法:
  from qcc_client import QccClient
  client = QccClient()
  info = client.get_company_registration_info("中粮集团有限公司")
"""

import json, re, os, sys, time
from pathlib import Path
from datetime import datetime

import requests

# ---- QCC MCP 端点 ----
QCC_ENDPOINTS = {
    'company':    'https://agent.qcc.com/mcp/company/stream',
    'ipr':        'https://agent.qcc.com/mcp/ipr/stream',
    'risk':       'https://agent.qcc.com/mcp/risk/stream',
    'operation':  'https://agent.qcc.com/mcp/operation/stream',
    'executive':  'https://agent.qcc.com/mcp/executive/stream',
}

SKILL_DIR = Path(__file__).parent.parent
CONFIG_PATH = SKILL_DIR / 'config.json'

# ---- 异常 ----
class QccAuthError(Exception):
    """企查查未授权"""
    def __init__(self, msg=None):
        super().__init__(msg or '企查查 OAuth 未授权。请运行: python oauth_qcc.py auth')

class QccCallError(Exception):
    """企查查调用失败"""
    pass


# ---- Token 管理（多服务器支持）----
# config.json 格式:
#   { "qcc_oauth": { "company": {access_token, refresh_token, ...}, "ipr": {...}, ... } }
#   或旧格式:      { "qcc_oauth": { access_token, refresh_token, ... } }  ← 视为 company
#
# 每个 QCC MCP 服务器需要独立授权（token 绑定到特定 resource URL）。
# 首次使用只需授权 company 服务器（覆盖 80% 数据），扩展服务器按需授权。

_token_cache = {}       # {server: token_string}
_token_cache_time = {}  # {server: timestamp}
_TOKEN_CACHE_TTL = 60   # 缓存 1 分钟


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise QccAuthError(
            '未找到 config.json。\n'
            '请先运行: python oauth_qcc.py auth'
        )
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_config(config: dict):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _get_server_token_info(server: str) -> dict:
    """
    从 config.json 中提取指定服务器的 token 信息。
    兼容旧格式（单 token → 视为 company）。
    """
    config = _load_config()
    oauth = config.get('qcc_oauth', {})

    # 新格式: qcc_oauth.company / qcc_oauth.ipr / ...
    if server in oauth and isinstance(oauth[server], dict) and 'access_token' in oauth[server]:
        return oauth[server]

    # 旧格式: qcc_oauth 本身是 token dict → 视为 company
    if 'access_token' in oauth:
        if server == 'company':
            return oauth
        # 其他服务器不能用 company token
        return {}

    return {}


def _save_server_token_info(server: str, token_info: dict):
    """保存指定服务器的 token 信息"""
    config = _load_config()
    if 'qcc_oauth' not in config:
        config['qcc_oauth'] = {}

    # 如果 qcc_oauth 本身是旧格式的 token dict，先迁移
    oauth = config['qcc_oauth']
    if 'access_token' in oauth:
        # 迁移: 旧 token → qcc_oauth.company
        config['qcc_oauth'] = {'company': oauth}

    config['qcc_oauth'][server] = token_info
    _save_config(config)


def get_bearer_token(server: str = 'company') -> str:
    """
    获取指定服务器的有效 Bearer token。支持自动刷新。

    Args:
        server: QCC 服务器名（company/ipr/risk/operation/executive）
    Returns:
        Bearer token string
    Raises:
        QccAuthError: 该服务器未授权
    """
    global _token_cache, _token_cache_time

    if server in _token_cache and time.time() - _token_cache_time.get(server, 0) < _TOKEN_CACHE_TTL:
        return _token_cache[server]

    sys.path.insert(0, str(Path(__file__).parent))
    from oauth_qcc import refresh_access_token, QCC_RESOURCES

    token_info = _get_server_token_info(server)
    access_token = token_info.get('access_token', '')
    refresh_token_val = token_info.get('refresh_token', '')
    client_id = token_info.get('client_id', '')
    obtained_at = token_info.get('_obtained_at', 0)
    expires_in = token_info.get('expires_in', 3600)
    redirect_uri = token_info.get('redirect_uri', '')

    if not access_token:
        if server == 'company':
            raise QccAuthError()
        else:
            raise QccAuthError(
                f'企查查 {server} 服务器未授权。请运行:\n'
                f'  python oauth_qcc.py auth --resource {server}'
            )

    # 有效且未过期 → 直接返回
    if obtained_at and time.time() < obtained_at + expires_in - 300:
        _token_cache[server] = access_token
        _token_cache_time[server] = time.time()
        return access_token

    # 尝试刷新
    if refresh_token_val and client_id:
        try:
            resource = QCC_RESOURCES.get(server, QCC_RESOURCES['company'])
            new_tokens = refresh_access_token(client_id, refresh_token_val, resource)
            new_tokens['client_id'] = client_id
            new_tokens['redirect_uri'] = redirect_uri
            _save_server_token_info(server, new_tokens)

            _token_cache[server] = new_tokens['access_token']
            _token_cache_time[server] = time.time()
            return _token_cache[server]
        except Exception:
            pass

    # 返回旧 token（可能已过期但值得一试）
    _token_cache[server] = access_token
    _token_cache_time[server] = time.time()
    return access_token


def clear_token_cache(server: str = None):
    """清除 token 缓存"""
    global _token_cache, _token_cache_time
    if server:
        _token_cache.pop(server, None)
        _token_cache_time.pop(server, None)
    else:
        _token_cache = {}
        _token_cache_time = {}


# ---- MCP 协议调用 ----

def _parse_sse_response(response_bytes: bytes) -> dict:
    """
    解析 QCC MCP 的 SSE 响应。
    格式: event: message\n data: <json-rpc-response>\n\n
    """
    text = response_bytes.decode('utf-8')
    m = re.search(r'data:\s*(\{.*\})', text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # 尝试直接 JSON 解析（某些响应可能不是 SSE）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'_raw': text[:500]}


def _call_mcp_server(server: str, tool_name: str, args: dict, timeout: int = 30) -> dict:
    """
    直接 HTTP 调用 QCC MCP 端点。
    MCP Streamable HTTP 协议: POST JSON-RPC → SSE 响应。

    Args:
        server: QCC 服务器名（company/ipr/risk/operation/executive）
        tool_name: MCP tool 名（如 get_company_registration_info）
        args: tool 参数（如 {'searchKey': '中粮集团有限公司'}）
        timeout: 超时秒数

    Returns:
        解析后的工具返回数据（dict）

    Raises:
        QccAuthError: 未授权
        QccCallError: 调用失败
    """
    endpoint = QCC_ENDPOINTS.get(server)
    if not endpoint:
        raise QccCallError(f'未知的 QCC MCP 服务器: {server}（可选: {", ".join(QCC_ENDPOINTS.keys())}）')

    # 获取 token（按服务器隔离，自动刷新）
    try:
        token = get_bearer_token(server)
    except QccAuthError:
        # 重新抛出，保留 server 信息（已在 get_bearer_token 中设置消息）
        raise

    rpc = {
        'jsonrpc': '2.0',
        'method': 'tools/call',
        'params': {
            'name': tool_name,
            'arguments': args,
        },
        'id': 1,
    }

    try:
        resp = requests.post(
            endpoint,
            json=rpc,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream, application/json',
            },
            timeout=timeout,
        )
    except requests.Timeout:
        raise QccCallError(f'{tool_name}: 调用超时 ({timeout}s)')
    except requests.ConnectionError as e:
        raise QccCallError(f'{tool_name}: 连接失败 — {e}')

    # Auth 错误
    if resp.status_code == 401:
        clear_token_cache(server)
        if server == 'company':
            raise QccAuthError(
                f'企查查 company token 无效。请运行:\n'
                f'  python oauth_qcc.py auth'
            )
        else:
            raise QccAuthError(
                f'企查查 {server} token 无效。请运行:\n'
                f'  python oauth_qcc.py auth --resource {server}'
            )

    if resp.status_code != 200:
        raise QccCallError(
            f'{tool_name}: HTTP {resp.status_code}\n{resp.text[:500]}'
        )

    # 解析 SSE 响应
    result = _parse_sse_response(resp.content)

    # JSON-RPC 错误
    if 'error' in result:
        err = result['error']
        msg = err.get('message', str(err))
        # 某些 "错误" 实际上是正常的（如"未发现记录"）
        if '未发现' in msg or '未匹配' in msg:
            return {'搜索结果': msg}
        raise QccCallError(f'{tool_name}: {msg}')

    # 提取 content → text → JSON
    r = result.get('result', {})
    extra = r.get('_extra', {})
    content = r.get('content', [])
    if content:
        text = content[0].get('text', '')
        if text:
            try:
                data = json.loads(text)
                # 保留 QCC 结算信息（积分/额度追踪）
                if extra:
                    data['_qcc_settlement'] = extra.get('settlement', '')
                return data
            except json.JSONDecodeError:
                return {'_raw_text': text, '_qcc_settlement': extra.get('settlement', '')}

    if extra:
        r['_qcc_settlement'] = extra.get('settlement', '')
    return r


# 向后兼容的顶层函数
def _call_qcc(tool_name: str, args: dict, timeout: int = 30) -> dict:
    """（已弃用）向后兼容。请使用 QccClient 实例方法。"""
    return _call_mcp_server('company', tool_name, args, timeout)


# ============================================================
# QccClient — 企查查 company 服务器（8 个核心工具）
# ============================================================

class QccClient:
    """企查查 MCP 客户端（company 服务器：工商/股东/高管/财务/投资/上市/分支/控制人）"""

    def __init__(self):
        self.server = 'company'
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_mcp_server(self.server, tool, args, timeout)
        self.call_log.append({
            'tool': tool, 'args': args,
            'time': datetime.now().strftime('%H:%M:%S'),
            'ok': '_raw' not in result and '_raw_text' not in result
        })
        return result

    # ---- 基础信息 ----

    def get_company_profile(self, name: str) -> dict:
        """企业简介 + 行业分类"""
        return self._call('get_company_profile', {'searchKey': name})

    def get_company_registration_info(self, name: str) -> dict:
        """核心工商登记信息（13 个字段）"""
        return self._call('get_company_registration_info', {'searchKey': name})

    def verify_company_accuracy(self, credit_code: str, name: str) -> dict:
        """核实企业名称与信用代码是否匹配"""
        return self._call('verify_company_accuracy', {'searchKey': credit_code, 'name': name})

    def get_company_by_query(self, keyword: str) -> dict:
        """模糊搜索企业（支持简称/品牌/股票简称）"""
        return self._call('get_company_by_query', {'searchKey': keyword})

    # ---- 股权与治理 ----

    def get_shareholder_info(self, name: str) -> dict:
        """股东构成 + 持股比例"""
        return self._call('get_shareholder_info', {'searchKey': name})

    def get_actual_controller(self, name: str) -> dict:
        """实际控制人（穿透后）"""
        return self._call('get_actual_controller', {'searchKey': name})

    def get_beneficial_owners(self, name: str) -> dict:
        """受益所有人（反洗钱）"""
        return self._call('get_beneficial_owners', {'searchKey': name})

    def get_key_personnel(self, name: str) -> dict:
        """高管团队（董事/监事/高管）"""
        return self._call('get_key_personnel', {'searchKey': name})

    # ---- 对外投资与变更 ----

    def get_external_investments(self, name: str) -> dict:
        """对外投资 / 子公司列表"""
        return self._call('get_external_investments', {'searchKey': name})

    def get_change_records(self, name: str) -> dict:
        """工商变更历史"""
        return self._call('get_change_records', {'searchKey': name})

    # ---- 财务 ----

    def get_financial_data(self, name: str) -> dict:
        """核心财务指标（资产负债/营收利润/偿债/营运/成长）"""
        return self._call('get_financial_data', {'searchKey': name})

    def get_annual_reports(self, name: str) -> dict:
        """工商年报（从业人数/股东变动/对外投资）"""
        return self._call('get_annual_reports', {'searchKey': name})

    # ---- 上市与分支 ----

    def get_listing_info(self, name: str) -> dict:
        """上市信息（股票代码/交易所/市值/总股本）"""
        return self._call('get_listing_info', {'searchKey': name})

    def get_branches(self, name: str) -> dict:
        """分支机构"""
        return self._call('get_branches', {'searchKey': name})

    # ---- 联系与税务 ----

    def get_contact_info(self, name: str, exclude_invalid: bool = False) -> dict:
        """公开联系方式（电话/邮箱/官网/ICP）"""
        args = {'searchKey': name}
        if exclude_invalid:
            args['excludeInvalidPhone'] = True
        return self._call('get_contact_info', args)

    def get_tax_invoice_info(self, name: str) -> dict:
        """开票信息（纳税人识别号/开户行/账号）"""
        return self._call('get_tax_invoice_info', {'searchKey': name})

    # ---- 便捷方法 ----

    def get_basic_snapshot(self, name: str) -> dict:
        """一站式基础快照：工商信息 + 股东 + 高管"""
        results = {}
        for tool, method in [
            ('registration', self.get_company_registration_info),
            ('shareholders', self.get_shareholder_info),
            ('personnel', self.get_key_personnel),
        ]:
            try:
                results[tool] = method(name)
            except (QccAuthError, QccCallError):
                raise
            except Exception as e:
                results[tool] = {'error': str(e)}
        return results

    def get_full_report(self, name: str) -> dict:
        """完整尽调：所有可用维度"""
        results = {}
        tools = [
            ('profile', self.get_company_profile),
            ('registration', self.get_company_registration_info),
            ('shareholders', self.get_shareholder_info),
            ('actual_controller', self.get_actual_controller),
            ('key_personnel', self.get_key_personnel),
            ('external_investments', self.get_external_investments),
            ('financial_data', self.get_financial_data),
            ('annual_reports', self.get_annual_reports),
            ('listing_info', self.get_listing_info),
            ('branches', self.get_branches),
            ('change_records', self.get_change_records),
        ]
        for key, method in tools:
            try:
                results[key] = method(name)
            except (QccAuthError, QccCallError):
                raise
            except Exception as e:
                results[key] = {'error': str(e)}
        return results


# ============================================================
# 扩展客户端基类
# ============================================================

class _QccExtensionClient:
    """扩展 MCP 服务器的基类（ipr/risk/operation/executive）"""

    def __init__(self, server: str):
        if server not in QCC_ENDPOINTS:
            raise QccCallError(f'未知 QCC 服务器: {server}')
        self.server = server
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_mcp_server(self.server, tool, args, timeout)
        self.call_log.append({
            'tool': tool, 'args': args,
            'time': datetime.now().strftime('%H:%M:%S'),
            'ok': '_raw' not in result and '_raw_text' not in result
        })
        return result


class QccIprClient(_QccExtensionClient):
    """企查查知识产权 MCP 客户端（qcc-ipr）"""
    def __init__(self):
        super().__init__('ipr')

    def get_app_info(self, name: str) -> dict:
        return self._call('get_app_info', {'searchKey': name})

    def get_commercial_franchise(self, name: str) -> dict:
        return self._call('get_commercial_franchise', {'searchKey': name})

    def get_all_ipr(self, name: str) -> dict:
        results = {}
        for tool, method in [
            ('app_info', self.get_app_info),
            ('franchise', self.get_commercial_franchise),
        ]:
            try:
                results[tool] = method(name)
            except Exception as e:
                results[tool] = {'error': str(e)}
        return results


class QccRiskClient(_QccExtensionClient):
    """企查查风险信息 MCP 客户端（qcc-risk）"""
    def __init__(self):
        super().__init__('risk')

    def get_administrative_penalty(self, name: str, date_from: str = None) -> dict:
        args = {'searchKey': name}
        if date_from:
            args['date_from'] = date_from
        return self._call('get_administrative_penalty', args)

    def get_bankruptcy_reorganization(self, name: str) -> dict:
        return self._call('get_bankruptcy_reorganization', {'searchKey': name})

    def get_all_risks(self, name: str) -> dict:
        from datetime import timedelta
        three_years_ago = (datetime.now() - timedelta(days=365*3)).strftime('%Y-%m-%d')
        results = {}
        for tool, method in [
            ('administrative_penalty', lambda n=name: self.get_administrative_penalty(n, three_years_ago)),
            ('bankruptcy', self.get_bankruptcy_reorganization),
        ]:
            try:
                results[tool] = method(name)
            except Exception as e:
                results[tool] = {'error': str(e)}
        return results


class QccOperationClient(_QccExtensionClient):
    """企查查经营动态 MCP 客户端（qcc-operation）"""
    def __init__(self):
        super().__init__('operation')

    def get_administrative_license(self, name: str) -> dict:
        return self._call('get_administrative_license', {'searchKey': name})

    def get_advertising_review(self, name: str) -> dict:
        return self._call('get_advertising_review', {'searchKey': name})

    def get_all_operations(self, name: str) -> dict:
        results = {}
        for tool, method in [
            ('administrative_license', self.get_administrative_license),
            ('advertising_review', self.get_advertising_review),
        ]:
            try:
                results[tool] = method(name)
            except Exception as e:
                results[tool] = {'error': str(e)}
        return results


class QccExecutiveClient(_QccExtensionClient):
    """企查查高管 MCP 客户端（qcc-executive）"""
    def __init__(self):
        super().__init__('executive')

    def get_executive_admin_penalty(self, company_name: str, person_name: str) -> dict:
        return self._call('get_executive_admin_penalty',
                         {'searchKey': company_name, 'personName': person_name})

    def get_executive_beneficial_owner(self, company_name: str, person_name: str) -> dict:
        return self._call('get_executive_beneficial_owner',
                         {'searchKey': company_name, 'personName': person_name})

    def get_all_executive_info(self, company_name: str, personnel: list[dict]) -> dict:
        results = {}
        for person in (personnel or [])[:3]:
            name = person.get('姓名', '')
            if name:
                for tool_key, method in [
                    ('admin_penalty', lambda n=name: self.get_executive_admin_penalty(company_name, n)),
                    ('beneficial_owner', lambda n=name: self.get_executive_beneficial_owner(company_name, n)),
                ]:
                    try:
                        key = f'{name}_{tool_key}'
                        results[key] = method()
                    except Exception as e:
                        results[key] = {'error': str(e)}
        return results


# ============================================================
# CLI 测试
# ============================================================
if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) < 2:
        print('用法: python qcc_client.py <企业名称> [工具名]')
        print('工具: profile | registration | shareholders | controller | personnel')
        print('      investments | finance | listing | branches | full')
        print('')
        print('前置条件: python oauth_qcc.py auth')
        sys.exit(1)

    company = sys.argv[1]
    tool = sys.argv[2] if len(sys.argv) > 2 else 'registration'

    client = QccClient()

    tool_map = {
        'profile': client.get_company_profile,
        'registration': client.get_company_registration_info,
        'shareholders': client.get_shareholder_info,
        'controller': client.get_actual_controller,
        'personnel': client.get_key_personnel,
        'investments': client.get_external_investments,
        'finance': client.get_financial_data,
        'listing': client.get_listing_info,
        'branches': client.get_branches,
        'snapshot': client.get_basic_snapshot,
        'full': client.get_full_report,
    }

    method = tool_map.get(tool)
    if not method:
        print(f'未知工具: {tool}')
        print(f'可用: {", ".join(tool_map.keys())}')
        sys.exit(1)

    try:
        result = method(company)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f'\n调用记录: {len(client.call_log)} 次')
    except QccAuthError as e:
        print(f'\n❌ 授权错误:\n{e}')
        sys.exit(2)
    except QccCallError as e:
        print(f'\n❌ 调用错误:\n{e}')
        sys.exit(3)
