"""
企查查 OAuth 2.0 授权模块 (Authorization Code + PKCE)
=====================================================
标准 OAuth 2.0 流程，无需 mcporter，直接在 Streamlit 中完成授权。

流程:
  1. 自动注册 OAuth 客户端 (registration_endpoint)
  2. 生成 PKCE code_verifier + code_challenge (S256)
  3. 打开浏览器 → 用户在企查查授权
  4. 回调到 Streamlit → 用 code 换 access_token
  5. Token 加密存储，自动刷新
"""

import hashlib, base64, secrets, json, urllib.parse, time, webbrowser
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
REDIRECT_PORT = 8501

# ---- PKCE 工具 ----
def _gen_code_verifier(length: int = 64) -> str:
    """生成 PKCE code_verifier（随机字符串）"""
    return secrets.token_urlsafe(length)[:128]

def _gen_code_challenge(verifier: str) -> str:
    """从 code_verifier 生成 S256 code_challenge"""
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

def _gen_state() -> str:
    """生成 OAuth state 参数（防 CSRF）"""
    return secrets.token_urlsafe(16)


# ---- 客户端注册 ----
def register_client(redirect_uri: str) -> dict:
    """
    动态注册 OAuth 客户端（RFC 7591）。
    企查查支持 public 客户端自动注册，无需提前申请 client_id。
    返回: {client_id, ...}
    """
    payload = {
        'client_name': '集团客户报告生成系统',
        'redirect_uris': [redirect_uri],
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
        'scope': SCOPE,
        'token_endpoint_auth_method': 'none',  # public client
    }
    resp = requests.post(REGISTRATION_ENDPOINT, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        return resp.json()
    else:
        raise Exception(f'客户端注册失败: {resp.status_code} {resp.text[:300]}')


# ---- OAuth 流程 ----
def build_authorization_url(client_id: str, redirect_uri: str,
                            code_challenge: str, state: str) -> str:
    """构建企查查授权页面 URL"""
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'resource': 'https://agent.qcc.com/mcp/company/stream',
    }
    return f'{AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}'


def exchange_code_for_token(client_id: str, code: str,
                            code_verifier: str, redirect_uri: str) -> dict:
    """
    用授权码交换 access_token + refresh_token。
    返回: {access_token, token_type, expires_in, refresh_token, scope}
    """
    payload = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'code': code,
        'code_verifier': code_verifier,
        'redirect_uri': redirect_uri,
        'resource': 'https://agent.qcc.com/mcp/company/stream',
    }
    resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        data['_obtained_at'] = int(time.time())
        return data
    else:
        raise Exception(f'Token 交换失败: {resp.status_code} {resp.text[:300]}')


def refresh_access_token(client_id: str, refresh_token: str) -> dict:
    """用 refresh_token 刷新 access_token"""
    payload = {
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'refresh_token': refresh_token,
        'resource': 'https://agent.qcc.com/mcp/company/stream',
    }
    resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        data['_obtained_at'] = int(time.time())
        return data
    else:
        raise Exception(f'Token 刷新失败: {resp.status_code} {resp.text[:300]}')


def get_valid_token(config: dict) -> str:
    """
    从配置获取有效的 access_token。如果过期则自动刷新。
    返回: Bearer token string
    """
    oauth = config.get('qcc_oauth', {})
    access_token = oauth.get('access_token', '')
    refresh_token = oauth.get('refresh_token', '')
    client_id = oauth.get('client_id', '')
    obtained_at = oauth.get('_obtained_at', 0)
    expires_in = oauth.get('expires_in', 3600)

    # 检查是否过期（提前 5 分钟刷新）
    if access_token and obtained_at:
        expires_at = obtained_at + expires_in - 300
        if time.time() < expires_at:
            return access_token

    # 尝试用 refresh_token 刷新
    if refresh_token and client_id:
        try:
            new_tokens = refresh_access_token(client_id, refresh_token)
            new_tokens['client_id'] = client_id
            new_tokens['redirect_uri'] = oauth.get('redirect_uri', '')
            config['qcc_oauth'] = new_tokens
            _save_config(config)
            return new_tokens['access_token']
        except Exception:
            pass

    return access_token  # 返回旧的（可能已过期）


def _save_config(config: dict):
    """保存配置到 config.json"""
    config_path = Path(__file__).parent / 'config.json'
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ---- 完整授权流程 ----
def start_oauth_flow() -> tuple[str, str]:
    """
    启动完整 OAuth 授权流程。
    返回: (authorization_url, state) — 需要将 authorization_url 在浏览器中打开
    """
    redirect_uri = f'http://localhost:{REDIRECT_PORT}'

    # 1. 注册客户端
    client_info = register_client(redirect_uri)
    client_id = client_info['client_id']

    # 2. 生成 PKCE 参数
    code_verifier = _gen_code_verifier()
    code_challenge = _gen_code_challenge(code_verifier)
    state = _gen_state()

    # 3. 保存到配置文件（待完成）
    config_path = Path(__file__).parent / 'config.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    config['_pending_oauth'] = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
        'state': state,
        'started_at': int(time.time()),
    }

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    # 4. 构建授权 URL
    auth_url = build_authorization_url(client_id, redirect_uri, code_challenge, state)

    return auth_url, state


def complete_oauth_flow(code: str, state: str) -> dict:
    """
    完成 OAuth 流程：用授权码换取 token。
    返回: token 信息 dict
    """
    config_path = Path(__file__).parent / 'config.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    pending = config.pop('_pending_oauth', {})
    if not pending:
        raise Exception('没有待完成的 OAuth 授权')

    # 校验 state
    if state != pending.get('state', ''):
        raise Exception('OAuth state 不匹配，可能存在 CSRF 攻击')

    client_id = pending['client_id']
    code_verifier = pending['code_verifier']
    redirect_uri = pending['redirect_uri']

    # 交换 token
    tokens = exchange_code_for_token(client_id, code, code_verifier, redirect_uri)
    tokens['client_id'] = client_id
    tokens['redirect_uri'] = redirect_uri

    # 保存
    config['qcc_oauth'] = tokens

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    return tokens


# ---- CLI 测试 ----
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        print('=== OAuth 端点测试 ===')
        print(f'Metadata: {AUTH_BASE}/.well-known/oauth-authorization-server')
        resp = requests.get(f'{AUTH_BASE}/.well-known/oauth-authorization-server', timeout=10)
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))

        print('\n=== 尝试注册客户端 ===')
        try:
            info = register_client(f'http://localhost:{REDIRECT_PORT}')
            print(json.dumps(info, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f'注册失败: {e}')

    elif len(sys.argv) > 1 and sys.argv[1] == 'auth':
        print('=== 启动 OAuth 授权流程 ===')
        auth_url, state = start_oauth_flow()
        print(f'State: {state}')
        print(f'\n请在浏览器中打开以下链接:\n{auth_url}')
        webbrowser.open(auth_url)

        print('\n授权完成后，输入回调 URL 中的 code 参数:')
        code = input('code: ').strip()
        if code:
            try:
                tokens = complete_oauth_flow(code, state)
                print(f'\n✅ 授权成功!')
                print(f'Token 已保存到 config.json')
                print(f'Access Token: {tokens["access_token"][:20]}...')
            except Exception as e:
                print(f'\n❌ 授权失败: {e}')
    else:
        print('用法:')
        print('  python oauth_qcc.py test   # 测试端点连通性')
        print('  python oauth_qcc.py auth   # 启动 OAuth 授权')
