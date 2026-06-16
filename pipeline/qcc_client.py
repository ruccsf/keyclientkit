"""
企查查 MCP 调用客户端
====================
通过 mcporter 调用企查查 MCP 工具，获取企业工商数据。

前置条件:
  1. mcporter 已安装（npm i -g mcporter）
  2. 企查查 MCP 已注册: mcporter config add qcc-company https://agent.qcc.com/mcp/company/stream
  3. OAuth 已授权: mcporter auth qcc-company

用法:
  from qcc_client import QccClient
  client = QccClient()
  info = client.get_company_registration_info("中粮集团有限公司")
"""

import subprocess, json, os, sys
from pathlib import Path
from datetime import datetime

MCPORTER = r"C:\Users\OseasyVM\AppData\Roaming\npm\mcporter.cmd"

# 可用的 QCC MCP 服务器
QCC_SERVERS = {
    'company':    'qcc-company',     # 工商/股东/高管/财务/投资/上市/分支/控制人
    'executive':  'qcc-executive',   # 高管个人处罚/受益所有人
    'history':    'qcc-history',     # 历史信息（需单独授权）
    'risk':       'qcc-risk',        # 行政处罚/破产重整
    'ipr':        'qcc-ipr',         # 知识产权（APP/特许经营/著作权/专利等）
    'operation':  'qcc-operation',   # 行政许可/广告审查/资产拍卖等
}
SERVER = QCC_SERVERS['company']  # 向后兼容

def _call_qcc_server(server: str, tool_name: str, args: dict, timeout: int = 30) -> dict:
    """底层调用：mcporter call <server> <tool> --args <json>"""
    args_json = json.dumps(args, ensure_ascii=False)
    cmd = [MCPORTER, "call", server, tool_name, "--args", args_json]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            timeout=timeout,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
    except subprocess.TimeoutExpired:
        raise QccCallError(f'{tool_name}: 调用超时 ({timeout}s)')
    except FileNotFoundError:
        raise QccCallError(f'mcporter 未找到: {MCPORTER}。请先安装: npm i -g mcporter')

    output = (result.stdout or '') + (result.stderr or '')

    if 'auth required' in output.lower() or 'unauthorized' in output.lower():
        raise QccAuthError(f'QCC {server} 未授权。请运行: mcporter auth {server}')

    if 'unknown mcp server' in output.lower():
        raise QccCallError(f'QCC {server} 未注册到 mcporter。')

    if result.returncode != 0:
        raise QccCallError(f'{tool_name}: mcporter 返回错误码 {result.returncode}\n{output[:500]}')

    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"_raw": result.stdout.strip()}
    return {}


class QccAuthError(Exception):
    """企查查未授权"""
    pass


class QccCallError(Exception):
    """企查查调用失败"""
    pass


def _call_qcc(tool_name: str, args: dict, timeout: int = 30) -> dict:
    """
    底层调用：mcporter call qcc-company <tool> --args <json>
    返回解析后的 dict
    """
    args_json = json.dumps(args, ensure_ascii=False)

    # 直接用 mcporter.cmd 作为可执行文件（不用 cmd /c，避免中文编码损坏）
    cmd = [MCPORTER, "call", SERVER, tool_name, "--args", args_json]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            timeout=timeout,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
    except subprocess.TimeoutExpired:
        raise QccCallError(f'{tool_name}: 调用超时 ({timeout}s)')
    except FileNotFoundError:
        raise QccCallError(f'mcporter 未找到: {MCPORTER}。请先安装: npm i -g mcporter')

    # 合并 stdout + stderr
    output = (result.stdout or '') + (result.stderr or '')

    # 检测未授权
    if 'auth required' in output.lower() or 'unauthorized' in output.lower():
        raise QccAuthError(
            f'企查查 MCP 未授权。请运行:\n'
            f'  mcporter auth {SERVER}\n'
            f'然后重新运行此命令。'
        )

    # 检测未知服务器
    if 'unknown mcp server' in output.lower():
        raise QccCallError(
            f'企查查 MCP 未注册到 mcporter。请运行:\n'
            f'  mcporter config add {SERVER} https://agent.qcc.com/mcp/company/stream\n'
            f'  mcporter auth {SERVER}'
        )

    if result.returncode != 0:
        raise QccCallError(f'{tool_name}: mcporter 返回错误码 {result.returncode}\n{output[:500]}')

    # 尝试解析 JSON
    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"_raw": result.stdout.strip()}
    return {}


# ============================================================
# 高级 API：每个 MCP 工具一个函数
# ============================================================

class QccClient:
    """企查查 MCP 客户端"""

    def __init__(self):
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_qcc(tool, args, timeout)
        self.call_log.append({
            'tool': tool, 'args': args,
            'time': datetime.now().strftime('%H:%M:%S'),
            'ok': '_raw' not in result and 'error' not in str(result).lower()[:100]
        })
        return result

    # ---- 基础信息 ----

    def get_company_profile(self, name: str) -> dict:
        """企业简介 + 行业分类"""
        return self._call('get_company_profile', {'searchKey': name})

    def get_company_registration_info(self, name: str) -> dict:
        """核心工商登记信息（13个字段）"""
        return self._call('get_company_registration_info', {'searchKey': name})

    def verify_company_accuracy(self, credit_code: str, name: str) -> dict:
        """核实企业名称与信用代码是否匹配（searchKey=信用代码, name=企业名称）"""
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
        """
        一站式基础快照：工商信息 + 股东 + 高管
        适合快速了解企业基本面
        """
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
        """
        完整尽调：所有可用维度
        返回包含 10+ 维度的企业全景数据
        """
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
# 扩展 API：知识产权 / 风险 / 经营 / 高管
# ============================================================

class QccIprClient:
    """企查查知识产权 MCP 客户端（qcc-ipr）"""
    def __init__(self):
        self.server = QCC_SERVERS['ipr']
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_qcc_server(self.server, tool, args, timeout)
        self.call_log.append({'tool': tool, 'args': args, 'time': datetime.now().strftime('%H:%M:%S')})
        return result

    def get_app_info(self, name: str) -> dict:
        """企业APP信息"""
        return self._call('get_app_info', {'searchKey': name})

    def get_commercial_franchise(self, name: str) -> dict:
        """商业特许经营备案"""
        return self._call('get_commercial_franchise', {'searchKey': name})

    def get_all_ipr(self, name: str) -> dict:
        """获取所有知识产权相关信息"""
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


class QccRiskClient:
    """企查查风险信息 MCP 客户端（qcc-risk）"""
    def __init__(self):
        self.server = QCC_SERVERS['risk']
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_qcc_server(self.server, tool, args, timeout)
        self.call_log.append({'tool': tool, 'args': args, 'time': datetime.now().strftime('%H:%M:%S')})
        return result

    def get_administrative_penalty(self, name: str, date_from: str = None) -> dict:
        """行政处罚记录"""
        args = {'searchKey': name}
        if date_from:
            args['date_from'] = date_from
        return self._call('get_administrative_penalty', args)

    def get_bankruptcy_reorganization(self, name: str) -> dict:
        """破产重整信息"""
        return self._call('get_bankruptcy_reorganization', {'searchKey': name})

    def get_all_risks(self, name: str) -> dict:
        """获取所有风险相关信息"""
        results = {}
        from datetime import timedelta
        three_years_ago = (datetime.now() - timedelta(days=365*3)).strftime('%Y-%m-%d')
        for tool, method in [
            ('administrative_penalty', lambda n: self.get_administrative_penalty(n, three_years_ago)),
            ('bankruptcy', self.get_bankruptcy_reorganization),
        ]:
            try:
                results[tool] = method(name)
            except Exception as e:
                results[tool] = {'error': str(e)}
        return results


class QccOperationClient:
    """企查查经营动态 MCP 客户端（qcc-operation）"""
    def __init__(self):
        self.server = QCC_SERVERS['operation']
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_qcc_server(self.server, tool, args, timeout)
        self.call_log.append({'tool': tool, 'args': args, 'time': datetime.now().strftime('%H:%M:%S')})
        return result

    def get_administrative_license(self, name: str) -> dict:
        """行政许可信息"""
        return self._call('get_administrative_license', {'searchKey': name})

    def get_advertising_review(self, name: str) -> dict:
        """广告审查信息"""
        return self._call('get_advertising_review', {'searchKey': name})

    def get_all_operations(self, name: str) -> dict:
        """获取所有经营动态"""
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


class QccExecutiveClient:
    """企查查高管 MCP 客户端（qcc-executive）"""
    def __init__(self):
        self.server = QCC_SERVERS['executive']
        self.call_log = []

    def _call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        result = _call_qcc_server(self.server, tool, args, timeout)
        self.call_log.append({'tool': tool, 'args': args, 'time': datetime.now().strftime('%H:%M:%S')})
        return result

    def get_executive_admin_penalty(self, company_name: str, person_name: str) -> dict:
        """高管个人行政处罚"""
        return self._call('get_executive_admin_penalty', {'searchKey': company_name, 'personName': person_name})

    def get_executive_beneficial_owner(self, company_name: str, person_name: str) -> dict:
        """高管受益所有人"""
        return self._call('get_executive_beneficial_owner', {'searchKey': company_name, 'personName': person_name})

    def get_all_executive_info(self, company_name: str, personnel: list[dict]) -> dict:
        """获取所有高管相关信息"""
        results = {}
        for person in (personnel or [])[:3]:  # 最多查3个关键高管
            name = person.get('姓名', '')
            if name:
                for tool, method in [
                    ('admin_penalty', lambda n=name: self.get_executive_admin_penalty(company_name, n)),
                    ('beneficial_owner', lambda n=name: self.get_executive_beneficial_owner(company_name, n)),
                ]:
                    try:
                        key = f'{name}_{tool}'
                        results[key] = method()
                    except Exception as e:
                        results[key] = {'error': str(e)}
        return results
if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) < 2:
        print('用法: python qcc_client.py <企业名称> [工具名]')
        print('工具: profile | registration | shareholders | controller | personnel')
        print('      investments | finance | listing | branches | full')
        print('')
        print('前置条件:')
        print('  1. mcporter config add qcc-company https://agent.qcc.com/mcp/company/stream')
        print('  2. mcporter auth qcc-company')
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
