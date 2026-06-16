"""
企查查 OAuth 2.0 授权模块 (Authorization Code + PKCE)
=====================================================
标准 OAuth 2.0 流程，直接 HTTP 调用，无需 mcporter。

用法:
  python oauth_qcc.py auth         # 一键授权（自动打开浏览器 + 本地回调服务器）
  python oauth_qcc.py auth --manual # 手动模式（复制链接到浏览器，粘贴回调 URL）
  python oauth_qcc.py test          # 测试端点连通性
  python oauth_qcc.py status        # 查看当前 token 状态

被 qcc_client.py 调用:
  from oauth_qcc import get_valid_token
  token = get_valid_token()  # 返回有效 Bearer token，过期自动刷新
"""

import hashlib, base64, secrets, json, urllib.parse, time, webbrowser, sys
from pathlib import Path
from datetime import datetime

import requests

# ---- OAuth 端点（来自 agent.qcc.com/.well-known/oauth-authorization-server）----
AUTH_BASE = 'https://agent.qcc.com'
AUTHORIZATION_ENDPOINT = f'{AUTH_BASE}/oauth/authorize'
TOKEN_ENDPOINT = f'{AUTH_BASE}/oauth/token'
REGISTRATION_ENDPOINT = f'{AUTH_BASE}/oauth/register'
REVOCATION_ENDPOINT = f'{AUTH_BASE}/oauth/revoke'
SCOPE = 'mcp:tools'
DEFAULT_RESOURCE = 'https://agent.qcc.com/mcp/company/stream'

# 所有可用的 QCC MCP 服务器资源
QCC_RESOURCES = {
    'company':    'https://agent.qcc.com/mcp/company/stream',
    'ipr':        'https://agent.qcc.com/mcp/ipr/stream',
    'risk':       'https://agent.qcc.com/mcp/risk/stream',
    'operation':  'https://agent.qcc.com/mcp/operation/stream',
    'executive':  'https://agent.qcc.com/mcp/executive/stream',
}

CONFIG_PATH = Path(__file__).parent / 'config.json'

# ---- PKCE 工具 ----
def _gen_code_verifier(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]

def _gen_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

def _gen_state() -> str:
    return secrets.token_urlsafe(16)


# ---- 配置读写 ----
def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'qcc_oauth': {}}

def _save_config(config: dict):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ---- 客户端注册 ----
def register_client(redirect_uri: str) -> dict:
    """动态注册 OAuth 客户端（RFC 7591）"""
    payload = {
        'client_name': '集团客户报告生成系统',
        'redirect_uris': [redirect_uri],
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
        'scope': SCOPE,
        'token_endpoint_auth_method': 'none',
    }
    resp = requests.post(REGISTRATION_ENDPOINT, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        return resp.json()
    else:
        raise Exception(f'客户端注册失败: {resp.status_code} {resp.text[:300]}')


# ---- OAuth 流程 ----
def build_authorization_url(client_id: str, redirect_uri: str,
                            code_challenge: str, state: str,
                            resource: str = DEFAULT_RESOURCE) -> str:
    """构建企查查授权页面 URL"""
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'resource': resource,
    }
    return f'{AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}'


def exchange_code_for_token(client_id: str, code: str,
                            code_verifier: str, redirect_uri: str,
                            resource: str = DEFAULT_RESOURCE) -> dict:
    """用授权码交换 access_token + refresh_token"""
    payload = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'code': code,
        'code_verifier': code_verifier,
        'redirect_uri': redirect_uri,
        'resource': resource,
    }
    resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        data['_obtained_at'] = int(time.time())
        return data
    else:
        raise Exception(f'Token 交换失败: {resp.status_code} {resp.text[:300]}')


def refresh_access_token(client_id: str, refresh_token: str,
                         resource: str = DEFAULT_RESOURCE) -> dict:
    """用 refresh_token 刷新 access_token"""
    payload = {
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'refresh_token': refresh_token,
        'resource': resource,
    }
    resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        data['_obtained_at'] = int(time.time())
        return data
    else:
        raise Exception(f'Token 刷新失败: {resp.status_code} {resp.text[:300]}')


def _get_server_name(resource: str) -> str:
    """根据 resource URL 反查服务器名"""
    for name, url in QCC_RESOURCES.items():
        if url == resource:
            return name
    return 'company'


def get_valid_token(resource: str = DEFAULT_RESOURCE) -> str:
    """
    从配置获取有效的 access_token。如果过期则自动刷新。
    如果完全未授权，抛出 QccAuthError。
    返回: Bearer token string
    兼容新旧两种 config 格式。
    """
    config = _load_config()
    oauth = config.get('qcc_oauth', {})

    # 确定服务器名 + 提取 token 信息
    server_name = _get_server_name(resource)

    # 新格式: qcc_oauth.{server}.access_token
    if server_name in oauth and isinstance(oauth[server_name], dict):
        token_info = oauth[server_name]
    # 旧格式: qcc_oauth.access_token（视为 company）
    elif 'access_token' in oauth:
        token_info = oauth
    else:
        raise QccAuthError()

    access_token = token_info.get('access_token', '')
    refresh_token_val = token_info.get('refresh_token', '')
    client_id = token_info.get('client_id', '')
    obtained_at = token_info.get('_obtained_at', 0)
    expires_in = token_info.get('expires_in', 3600)
    redirect_uri = token_info.get('redirect_uri', '')

    if not access_token:
        raise QccAuthError()

    # 检查是否过期（提前 5 分钟刷新）
    if obtained_at and time.time() < obtained_at + expires_in - 300:
        return access_token

    # 尝试用 refresh_token 刷新
    if refresh_token_val and client_id:
        try:
            new_tokens = refresh_access_token(client_id, refresh_token_val, resource)
            new_tokens['client_id'] = client_id
            new_tokens['redirect_uri'] = redirect_uri
            # 保存到对应服务器名下
            oauth[server_name] = new_tokens
            config['qcc_oauth'] = oauth
            _save_config(config)
            return new_tokens['access_token']
        except Exception:
            pass

    # 返回旧的（可能已过期但值得一试）
    return access_token


class QccAuthError(Exception):
    """企查查未授权 — 需运行 python oauth_qcc.py auth"""
    def __init__(self, msg=None):
        super().__init__(msg or '企查查 OAuth 未授权。请运行: python oauth_qcc.py auth')


# ---- 一键授权（本地回调服务器）----

def _find_free_port(start: int = 8501, max_attempts: int = 10) -> int:
    """找一个空闲端口"""
    import socket
    for port in range(start, start + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return start  # fallback


def run_auto_auth(port: int = None, resource: str = DEFAULT_RESOURCE) -> dict:
    """
    一键 OAuth 授权：启动本地 HTTP 服务器 → 打开浏览器 → 等待回调 → 换 token。

    返回: token 信息 dict
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    if port is None:
        port = _find_free_port()

    redirect_uri = f'http://localhost:{port}/callback'

    # 1. 注册客户端
    print('📋 注册 OAuth 客户端...')
    client_info = register_client(redirect_uri)
    client_id = client_info['client_id']
    print(f'   client_id: {client_id}')

    # 2. 生成 PKCE 参数
    code_verifier = _gen_code_verifier()
    code_challenge = _gen_code_challenge(code_verifier)
    state = _gen_state()

    # 3. 构建授权 URL
    auth_url = build_authorization_url(client_id, redirect_uri, code_challenge, state, resource)

    # 4. 启动本地 HTTP 服务器
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            code = qs.get('code', [None])[0]
            got_state = qs.get('state', [None])[0]

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()

            if code and got_state == state:
                self.server.auth_code = code
                self.server.auth_done = True
                self.wfile.write(
                    '<html><body style="font-family:sans-serif;text-align:center;padding-top:80px">'
                    '<h2>✅ 授权成功！</h2>'
                    '<p>Token 已保存到 config.json，可以关闭此页面。</p>'
                    '</body></html>'.encode('utf-8')
                )
            else:
                self.server.auth_code = None
                self.server.auth_done = True
                self.wfile.write(
                    '<html><body style="font-family:sans-serif;text-align:center;padding-top:80px">'
                    '<h2>❌ 授权失败</h2>'
                    f'<p>state 不匹配或缺少 code 参数</p>'
                    '</body></html>'.encode('utf-8')
                )

        def log_message(self, format, *args):
            pass  # 抑制日志

    server = HTTPServer(('127.0.0.1', port), CallbackHandler)
    server.auth_done = False
    server.auth_code = None
    server.timeout = 5  # handle_request timeout

    print(f'🌐 启动回调服务器: {redirect_uri}')
    print()

    # 5. 打开浏览器
    print('🔗 正在打开授权页面...')
    print(f'   （如未自动打开，请手动复制以下链接到浏览器）')
    print(f'   {auth_url}')
    print()
    webbrowser.open(auth_url)

    # 6. 等待回调
    print('⏳ 等待授权完成（120 秒超时）...')
    deadline = time.time() + 120
    while not server.auth_done and time.time() < deadline:
        server.handle_request()

    server.server_close()

    if not server.auth_code:
        raise Exception('授权超时或失败：未收到浏览器回调。请重试。')

    print(f'✅ 收到授权码')

    # 7. 交换 token
    print('🔄 交换 Token...')
    tokens = exchange_code_for_token(client_id, server.auth_code, code_verifier, redirect_uri, resource)
    tokens['client_id'] = client_id
    tokens['redirect_uri'] = redirect_uri

    # 8. 保存（多服务器格式: qcc_oauth.<server_name>）
    config = _load_config()
    # 确定服务器名
    server_name = 'company'
    for name, url in QCC_RESOURCES.items():
        if url == resource:
            server_name = name
            break
    # 迁移旧格式
    oauth = config.get('qcc_oauth', {})
    if 'access_token' in oauth:
        config['qcc_oauth'] = {'company': oauth}  # 旧 token 归为 company
    config.setdefault('qcc_oauth', {})[server_name] = tokens
    _save_config(config)

    print(f'💾 Token 已保存到 config.json')
    print(f'   access_token:  {tokens["access_token"][:30]}...')
    print(f'   expires_in:    {tokens["expires_in"]} 秒')
    print(f'   refresh_token: {tokens["refresh_token"][:30]}...')
    print()
    print('✅ 授权完成！现在可以运行数据采集了。')

    return tokens


def run_manual_auth(resource: str = DEFAULT_RESOURCE) -> dict:
    """
    手动 OAuth 授权：打印授权链接 → 用户粘贴回调 URL → 交换 token。
    适用于无法启动本地服务器的情况（如远程终端、部分 IDE）。
    """
    redirect_uri = f'http://localhost:8501'

    print('📋 注册 OAuth 客户端...')
    client_info = register_client(redirect_uri)
    client_id = client_info['client_id']

    code_verifier = _gen_code_verifier()
    code_challenge = _gen_code_challenge(code_verifier)
    state = _gen_state()

    auth_url = build_authorization_url(client_id, redirect_uri, code_challenge, state, resource)

    print()
    print('=' * 60)
    print('请在浏览器中打开以下链接并完成授权：')
    print()
    print(auth_url)
    print()
    print('=' * 60)
    print()
    print('授权完成后，浏览器会重定向到 http://localhost:8501/?code=...&state=...')
    print('（页面可能无法打开，没关系。）')
    print()
    print('请将浏览器地址栏中的完整 URL 粘贴到此处：')
    callback_url = input('> ').strip()

    # 解析回调 URL
    parsed = urllib.parse.urlparse(callback_url)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get('code', [None])[0]
    got_state = qs.get('state', [None])[0]

    if not code:
        raise Exception('回调 URL 中未找到 code 参数，请检查。')
    if got_state != state:
        raise Exception(f'state 不匹配（期望 {state}，实际 {got_state}），可能存在安全风险。')

    print('🔄 交换 Token...')
    tokens = exchange_code_for_token(client_id, code, code_verifier, redirect_uri, resource)
    tokens['client_id'] = client_id
    tokens['redirect_uri'] = redirect_uri

    # 保存（多服务器格式）
    config = _load_config()
    server_name = 'company'
    for name, url in QCC_RESOURCES.items():
        if url == resource:
            server_name = name
            break
    oauth = config.get('qcc_oauth', {})
    if 'access_token' in oauth:
        config['qcc_oauth'] = {'company': oauth}
    config.setdefault('qcc_oauth', {})[server_name] = tokens
    _save_config(config)


def print_token_status():
    """打印当前 token 状态（兼容新旧格式，显示所有已授权服务器）"""
    config = _load_config()
    oauth = config.get('qcc_oauth', {})

    # 收集所有已授权的服务器
    servers = {}
    # 新格式: qcc_oauth.{server}.access_token
    for name in QCC_RESOURCES:
        if name in oauth and isinstance(oauth[name], dict) and oauth[name].get('access_token'):
            servers[name] = oauth[name]
    # 旧格式: qcc_oauth.access_token（视为 company）
    if not servers and 'access_token' in oauth:
        servers['company'] = oauth

    if not servers:
        print('❌ 未授权 — 请运行: python oauth_qcc.py auth')
        return

    for server_name, token_info in servers.items():
        access_token = token_info['access_token']
        obtained_at = token_info.get('_obtained_at', 0)
        expires_in = token_info.get('expires_in', 3600)

        # 解码 JWT
        parts = access_token.split('.')
        if len(parts) == 3:
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            try:
                decoded = json.loads(base64.urlsafe_b64decode(payload))
            except Exception:
                decoded = {}
        else:
            decoded = {}

        now = time.time()
        expires_at = obtained_at + expires_in
        is_valid = now < expires_at - 300

        print(f'\n[{server_name}] {"✅ Token 有效" if is_valid else "⚠️  Token 已过期（下次使用时会自动刷新）"}')
        print(f'   资源:        {decoded.get("resource", "?")}')
        print(f'   作用域:      {decoded.get("scope", "?")}')
        print(f'   签发时间:     {datetime.fromtimestamp(decoded.get("iat", obtained_at)).strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'   过期时间:     {datetime.fromtimestamp(decoded.get("exp", expires_at)).strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'   过期倒计时:   {int((expires_at - now) // 60)} 分钟')
        print(f'   refresh_token: {"有" if token_info.get("refresh_token") else "无"}')


# ---- CLI ----
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法:')
        print('  python oauth_qcc.py auth         一键 OAuth 授权（推荐）')
        print('  python oauth_qcc.py auth --manual 手动模式（复制粘贴 URL）')
        print('  python oauth_qcc.py status       查看 token 状态')
        print('  python oauth_qcc.py test         测试 API 端点连通性')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'test':
        print('=== OAuth 端点测试 ===')
        print(f'Metadata: {AUTH_BASE}/.well-known/oauth-authorization-server')
        resp = requests.get(f'{AUTH_BASE}/.well-known/oauth-authorization-server', timeout=10)
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))

        print('\n=== 尝试注册客户端 ===')
        try:
            info = register_client(f'http://localhost:8501')
            print(json.dumps(info, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f'注册失败: {e}')

    elif cmd == 'auth':
        manual = '--manual' in sys.argv
        resource = DEFAULT_RESOURCE
        # 支持 --resource <name> 指定其他服务器
        for i, arg in enumerate(sys.argv):
            if arg == '--resource' and i + 1 < len(sys.argv):
                res_name = sys.argv[i + 1]
                resource = QCC_RESOURCES.get(res_name, resource)

        try:
            if manual:
                run_manual_auth(resource)
            else:
                run_auto_auth(resource=resource)
        except Exception as e:
            print(f'\n❌ 授权失败: {e}')
            sys.exit(1)

    elif cmd == 'status':
        print_token_status()

    else:
        print(f'未知命令: {cmd}')
        sys.exit(1)
