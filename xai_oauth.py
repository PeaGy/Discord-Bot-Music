"""
SuperGrok / xAI OAuth (PKCE) — login 1 lần, lưu token local, auto-refresh.

Public client_id của Grok CLI / community tools (không phải secret).
Dùng Bearer access_token gọi https://api.x.ai/v1 (Responses / chat).

CLI:
  python -m xai_oauth login
  python -m xai_oauth status
  python -m xai_oauth logout
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hằng số OAuth (public desktop client — không phải secret)
# ---------------------------------------------------------------------------
XAI_ISSUER = "https://auth.x.ai"
XAI_DISCOVERY_URL = f"{XAI_ISSUER}/.well-known/openid-configuration"
XAI_AUTHORIZE_URL = f"{XAI_ISSUER}/oauth2/authorize"
XAI_TOKEN_URL_DEFAULT = f"{XAI_ISSUER}/oauth2/token"
XAI_API_BASE = "https://api.x.ai/v1"

# Client ID public của Grok CLI / SuperGrok OAuth flow
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_SCOPE = (
    "openid profile email offline_access "
    "grok-cli:access api:access "
    "conversations:read conversations:write "
    "workspaces:read workspaces:write"
)

DEFAULT_REDIRECT_HOST = "127.0.0.1"
DEFAULT_REDIRECT_PORT = 56121
DEFAULT_REDIRECT_PATH = "/callback"
OAUTH_CALLBACK_TIMEOUT_S = 180
# Refresh trước khi hết hạn (giây)
ACCESS_TOKEN_SKEW_S = 120

# File token mặc định cạnh process (project root)
DEFAULT_TOKEN_PATH = Path(os.getenv("XAI_TOKEN_PATH", ".xai_tokens.json"))
# Session Grok CLI sẵn có trên máy (nếu user đã login Grok Build / CLI)
GROK_CLI_AUTH_PATH = Path.home() / ".grok" / "auth.json"


class XaiOAuthError(RuntimeError):
    """Lỗi OAuth / token xAI."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float  # unix epoch seconds
    token_endpoint: str = XAI_TOKEN_URL_DEFAULT
    client_id: str = XAI_CLIENT_ID

    def is_expired(self, skew_s: float = ACCESS_TOKEN_SKEW_S) -> bool:
        return time.time() >= (self.expires_at - skew_s)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_endpoint": self.token_endpoint,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenBundle:
        expires_at = data.get("expires_at")
        if expires_at is None:
            expires_at = time.time() + float(data.get("expires_in", 0) or 0)
        else:
            expires_at = float(expires_at)
            # Hỗ trợ ISO string nếu lỡ lưu dạng đó
            if expires_at > 1e12:
                expires_at = expires_at / 1000.0
        return cls(
            access_token=str(data.get("access_token") or data.get("key") or "").strip(),
            refresh_token=str(data.get("refresh_token") or "").strip(),
            expires_at=expires_at,
            token_endpoint=str(
                data.get("token_endpoint") or XAI_TOKEN_URL_DEFAULT
            ).strip(),
            client_id=str(data.get("client_id") or data.get("oidc_client_id") or XAI_CLIENT_ID).strip(),
        )


def _project_token_path() -> Path:
    raw = os.getenv("XAI_TOKEN_PATH", str(DEFAULT_TOKEN_PATH))
    return Path(raw).expanduser()


def _file_lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


class _FileLock:
    """Lock đơn giản cho Windows/Unix khi ghi token (tránh race refresh)."""

    def __init__(self, path: Path, timeout_s: float = 10.0):
        self.path = path
        self.timeout_s = timeout_s
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout_s
        while True:
            try:
                self._fh = open(self.path, "a+b")
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError):
                if self._fh:
                    try:
                        self._fh.close()
                    except Exception:
                        pass
                    self._fh = None
                if time.time() >= deadline:
                    raise XaiOAuthError("Không lấy được file lock cho token store.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if not self._fh:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None


def _parse_expires_at_iso_or_unix(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    if not text:
        return 0.0
    # epoch number as string
    try:
        v = float(text)
        return v / 1000.0 if v > 1e12 else v
    except ValueError:
        pass
    # ISO-8601, e.g. 2026-08-11T00:46:53.718585400Z
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # trim subsecond > 6 digits
        if "." in text:
            head, rest = text.split(".", 1)
            frac = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            frac = (frac + "000000")[:6]
            text = f"{head}.{frac}{tz}"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def load_tokens_from_grok_cli(path: Path | None = None) -> TokenBundle | None:
    """Đọc ~/.grok/auth.json (Grok CLI) nếu có session hợp lệ."""
    path = path or GROK_CLI_AUTH_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Không đọc được %s", path)
        return None

    # Format: { "https://auth.x.ai::<client_id>": { key, refresh_token, expires_at, ... } }
    if not isinstance(data, dict):
        return None

    candidates: list[dict[str, Any]] = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if "refresh_token" not in entry and "key" not in entry:
            continue
        if "auth.x.ai" in str(key) or entry.get("oidc_issuer") == XAI_ISSUER:
            candidates.append(entry)
        elif entry.get("refresh_token") and (entry.get("key") or entry.get("access_token")):
            candidates.append(entry)

    if not candidates:
        # Fallback: single entry object
        if data.get("refresh_token") and (data.get("key") or data.get("access_token")):
            candidates = [data]
        else:
            return None

    entry = candidates[0]
    access = str(entry.get("access_token") or entry.get("key") or "").strip()
    refresh = str(entry.get("refresh_token") or "").strip()
    if not access or not refresh:
        return None

    expires_at = _parse_expires_at_iso_or_unix(entry.get("expires_at"))
    if expires_at <= 0:
        expires_at = time.time() + 300

    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        token_endpoint=XAI_TOKEN_URL_DEFAULT,
        client_id=str(
            entry.get("oidc_client_id") or entry.get("client_id") or XAI_CLIENT_ID
        ),
    )


def load_tokens(path: Path | None = None) -> TokenBundle | None:
    """Ưu tiên file project, sau đó ~/.grok/auth.json."""
    path = path or _project_token_path()
    if path.is_file():
        try:
            with _FileLock(_file_lock_path(path)):
                raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and (raw.get("access_token") or raw.get("key")):
                bundle = TokenBundle.from_dict(raw)
                if bundle.access_token and bundle.refresh_token:
                    return bundle
        except (OSError, json.JSONDecodeError, XaiOAuthError) as e:
            logger.warning("Không đọc được token project (%s): %s", path, e)

    return load_tokens_from_grok_cli()


def save_tokens(bundle: TokenBundle, path: Path | None = None) -> None:
    path = path or _project_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False)
    with _FileLock(_file_lock_path(path)):
        path.write_text(payload + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def clear_tokens(path: Path | None = None) -> None:
    path = path or _project_token_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        logger.warning("Không xoá được %s: %s", path, e)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _validate_xai_url(url: str, field: str = "endpoint") -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise XaiOAuthError(f"xAI OAuth {field} không dùng HTTPS: {url}")
    host = (parsed.hostname or "").lower()
    if host != "x.ai" and not host.endswith(".x.ai"):
        raise XaiOAuthError(f"xAI OAuth {field} host không hợp lệ: {host}")
    return url


async def discover_token_endpoint(session: aiohttp.ClientSession | None = None) -> str:
    close = False
    if session is None:
        session = aiohttp.ClientSession()
        close = True
    try:
        async with session.get(
            XAI_DISCOVERY_URL, headers={"Accept": "application/json"}
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    "OIDC discovery HTTP %s — dùng token endpoint mặc định", resp.status
                )
                return XAI_TOKEN_URL_DEFAULT
            data = await resp.json()
            endpoint = str(data.get("token_endpoint") or "").strip()
            if not endpoint:
                return XAI_TOKEN_URL_DEFAULT
            return _validate_xai_url(endpoint, "token_endpoint")
    except Exception:
        logger.exception("OIDC discovery thất bại — dùng endpoint mặc định")
        return XAI_TOKEN_URL_DEFAULT
    finally:
        if close:
            await session.close()


def build_authorize_url(
    *,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    nonce: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": XAI_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": XAI_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "plan": "generic",
        "referrer": "hermes-agent",
    }
    return f"{XAI_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _parse_token_response(
    payload: dict[str, Any],
    *,
    started_at: float,
    token_endpoint: str,
    fallback_refresh: str = "",
) -> TokenBundle:
    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or fallback_refresh).strip()
    if not access:
        raise XaiOAuthError("Token response thiếu access_token.")
    if not refresh:
        raise XaiOAuthError("Token response thiếu refresh_token.")

    expires_in = payload.get("expires_in")
    if expires_in is not None:
        expires_at = started_at + float(expires_in)
    else:
        # JWT exp fallback
        expires_at = started_at + 3600
        try:
            parts = access.split(".")
            if len(parts) >= 2:
                pad = "=" * (-len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
                if "exp" in claims:
                    expires_at = float(claims["exp"])
        except Exception:
            pass

    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        token_endpoint=token_endpoint,
        client_id=XAI_CLIENT_ID,
    )


async def exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    code_challenge: str,
    token_endpoint: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> TokenBundle:
    close = False
    if session is None:
        session = aiohttp.ClientSession()
        close = True
    try:
        endpoint = token_endpoint or await discover_token_endpoint(session)
        endpoint = _validate_xai_url(endpoint, "token_endpoint")
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": XAI_CLIENT_ID,
            "code_verifier": code_verifier,
            # xAI yêu cầu echo code_challenge khi exchange (Hermes/OpenCode)
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        started = time.time()
        async with session.post(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise XaiOAuthError(
                    f"Đổi authorization code thất bại (HTTP {resp.status}): {text[:500]}"
                )
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as e:
                raise XaiOAuthError("Token response không phải JSON.") from e
        return _parse_token_response(
            payload, started_at=started, token_endpoint=endpoint
        )
    finally:
        if close:
            await session.close()


async def refresh_tokens(
    bundle: TokenBundle,
    *,
    session: aiohttp.ClientSession | None = None,
) -> TokenBundle:
    close = False
    if session is None:
        session = aiohttp.ClientSession()
        close = True
    try:
        endpoint = _validate_xai_url(
            bundle.token_endpoint or XAI_TOKEN_URL_DEFAULT, "token_endpoint"
        )
        body = {
            "grant_type": "refresh_token",
            "client_id": bundle.client_id or XAI_CLIENT_ID,
            "refresh_token": bundle.refresh_token,
        }
        started = time.time()
        async with session.post(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise XaiOAuthError(
                    f"Refresh token thất bại (HTTP {resp.status}): {text[:500]}"
                )
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as e:
                raise XaiOAuthError("Refresh response không phải JSON.") from e
        return _parse_token_response(
            payload,
            started_at=started,
            token_endpoint=endpoint,
            fallback_refresh=bundle.refresh_token,
        )
    finally:
        if close:
            await session.close()


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler một shot nhận ?code=&state=."""

    result: dict[str, str] | None = None
    expected_state: str = ""
    event: threading.Event | None = None

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # im lặng

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != DEFAULT_REDIRECT_PATH.rstrip("/"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        qs = urllib.parse.parse_qs(parsed.query)
        err = (qs.get("error") or [None])[0]
        if err:
            desc = (qs.get("error_description") or [err])[0]
            _OAuthCallbackHandler.result = {"error": str(desc)}
            body = (
                "<html><body><h2>Đăng nhập SuperGrok thất bại</h2>"
                f"<p>{urllib.parse.quote(str(desc), safe='')}</p>"
                "<p>Bạn có thể đóng tab này.</p></body></html>"
            ).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if _OAuthCallbackHandler.event:
                _OAuthCallbackHandler.event.set()
            return

        code = (qs.get("code") or [None])[0]
        state = (qs.get("state") or [None])[0]
        if not code:
            _OAuthCallbackHandler.result = {"error": "Thiếu authorization code."}
        elif state != _OAuthCallbackHandler.expected_state:
            _OAuthCallbackHandler.result = {"error": "OAuth state không khớp."}
        else:
            _OAuthCallbackHandler.result = {"code": code, "state": state or ""}

        ok = "error" not in (_OAuthCallbackHandler.result or {})
        title = "Đăng nhập SuperGrok thành công" if ok else "Đăng nhập SuperGrok thất bại"
        msg = (
            "Bạn có thể đóng tab này và quay lại terminal."
            if ok
            else (_OAuthCallbackHandler.result or {}).get("error", "Lỗi không rõ")
        )
        body = (
            f"<html><body><h2>{title}</h2><p>{msg}</p></body></html>"
        ).encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if _OAuthCallbackHandler.event:
            _OAuthCallbackHandler.event.set()


def _start_callback_server(
    host: str, port: int, state: str
) -> tuple[HTTPServer, threading.Event]:
    event = threading.Event()
    _OAuthCallbackHandler.result = None
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.event = event

    class ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    try:
        server = ReuseHTTPServer((host, port), _OAuthCallbackHandler)
    except OSError:
        # Port bận → port ngẫu nhiên
        server = ReuseHTTPServer((host, 0), _OAuthCallbackHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, event


async def login_interactive(
    *,
    open_browser: bool = True,
    token_path: Path | None = None,
) -> TokenBundle:
    """
    Chạy PKCE loopback trên máy local: mở browser → user login SuperGrok →
    lưu token vào file project.
    """
    verifier, challenge = generate_pkce()
    state = secrets.token_hex(24)
    nonce = secrets.token_hex(24)

    server, event = _start_callback_server(
        DEFAULT_REDIRECT_HOST, DEFAULT_REDIRECT_PORT, state
    )
    try:
        bound_port = server.server_address[1]
        redirect_uri = (
            f"http://{DEFAULT_REDIRECT_HOST}:{bound_port}{DEFAULT_REDIRECT_PATH}"
        )
        auth_url = build_authorize_url(
            redirect_uri=redirect_uri,
            code_challenge=challenge,
            state=state,
            nonce=nonce,
        )

        print("=" * 60)
        print("SuperGrok / xAI OAuth login")
        print("=" * 60)
        print(f"Redirect: {redirect_uri}")
        print()
        print("Mở URL sau trên trình duyệt (nếu browser chưa tự mở):")
        print(auth_url)
        print()
        print(f"Chờ callback tối đa {OAUTH_CALLBACK_TIMEOUT_S}s...")

        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                logger.warning("Không mở được browser tự động.")

        ok = await asyncio.to_thread(event.wait, OAUTH_CALLBACK_TIMEOUT_S)
        if not ok:
            raise XaiOAuthError(
                "Hết thời gian chờ đăng nhập. Chạy lại: python -m xai_oauth login"
            )

        result = _OAuthCallbackHandler.result or {}
        if "error" in result:
            raise XaiOAuthError(f"OAuth callback lỗi: {result['error']}")
        code = result.get("code")
        if not code:
            raise XaiOAuthError("Không nhận được authorization code.")

        async with aiohttp.ClientSession() as session:
            token_endpoint = await discover_token_endpoint(session)
            bundle = await exchange_code_for_tokens(
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=verifier,
                code_challenge=challenge,
                token_endpoint=token_endpoint,
                session=session,
            )

        save_tokens(bundle, path=token_path)
        print(f"✅ Đã lưu token vào {_project_token_path() if token_path is None else token_path}")
        return bundle
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


class XaiOAuth:
    """
    Quản lý access token cho bot.

    Ưu tiên:
      1. SuperGrok OAuth (file project hoặc ~/.grok/auth.json)
      2. XAI_API_KEY (fallback pay-as-you-go, optional)
    """

    def __init__(self, token_path: Path | None = None):
        self.token_path = token_path or _project_token_path()
        self._bundle: TokenBundle | None = None
        self._lock = asyncio.Lock()
        self._api_key = (os.getenv("XAI_API_KEY") or "").strip() or None

    def is_authenticated(self) -> bool:
        if self._api_key:
            return True
        bundle = self._bundle or load_tokens(self.token_path)
        return bool(bundle and bundle.access_token and bundle.refresh_token)

    def auth_mode(self) -> str:
        if self._bundle or load_tokens(self.token_path):
            return "oauth"
        if self._api_key:
            return "api_key"
        return "none"

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Trả Bearer token hợp lệ (refresh nếu cần)."""
        if self._api_key and not force_refresh:
            # API key path — không OAuth
            # Vẫn cho phép force_refresh chỉ với OAuth; với api key trả luôn
            pass

        async with self._lock:
            # Ưu tiên OAuth nếu có token
            if self._bundle is None:
                self._bundle = load_tokens(self.token_path)

            if self._bundle is not None:
                if force_refresh or self._bundle.is_expired():
                    try:
                        async with aiohttp.ClientSession() as session:
                            new_bundle = await refresh_tokens(
                                self._bundle, session=session
                            )
                        self._bundle = new_bundle
                        # Chỉ ghi vào project path (không ghi đè ~/.grok)
                        save_tokens(self._bundle, path=self.token_path)
                        logger.info("Đã refresh SuperGrok access token.")
                    except XaiOAuthError:
                        # Nếu refresh fail và có API key fallback
                        if self._api_key:
                            logger.warning(
                                "Refresh OAuth thất bại — fallback XAI_API_KEY."
                            )
                            return self._api_key
                        raise
                return self._bundle.access_token

            if self._api_key:
                return self._api_key

            raise XaiOAuthError(
                "Chưa đăng nhập SuperGrok. Chạy: python -m xai_oauth login\n"
                "Hoặc đặt XAI_API_KEY trong .env (pay-as-you-go)."
            )

    async def ensure_ready(self) -> bool:
        """True nếu lấy được token; False nếu chưa auth (không raise)."""
        try:
            await self.get_access_token()
            return True
        except XaiOAuthError:
            return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_login(args: argparse.Namespace) -> int:
    try:
        asyncio.run(
            login_interactive(
                open_browser=not args.no_browser,
                token_path=Path(args.path) if args.path else None,
            )
        )
        return 0
    except XaiOAuthError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nĐã huỷ.", file=sys.stderr)
        return 130


def _cmd_status(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else _project_token_path()
    api_key = (os.getenv("XAI_API_KEY") or "").strip()
    bundle = load_tokens(path)

    print(f"Project token file: {path} ({'có' if path.is_file() else 'không'})")
    print(
        f"Grok CLI auth: {GROK_CLI_AUTH_PATH} "
        f"({'có' if GROK_CLI_AUTH_PATH.is_file() else 'không'})"
    )
    print(f"XAI_API_KEY env: {'có' if api_key else 'không'}")

    if bundle:
        remaining = int(bundle.expires_at - time.time())
        status = "hết hạn" if remaining <= 0 else f"còn ~{remaining}s"
        print(f"OAuth access_token: có ({status})")
        print(f"OAuth refresh_token: {'có' if bundle.refresh_token else 'không'}")
        print(f"expires_at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(bundle.expires_at))}")
        return 0

    if api_key:
        print("Sẽ dùng XAI_API_KEY (không OAuth).")
        return 0

    print("Chưa có credentials. Chạy: python -m xai_oauth login")
    return 1


def _cmd_logout(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else _project_token_path()
    clear_tokens(path)
    print(f"Đã xoá token project: {path}")
    print("(Không đụng ~/.grok/auth.json — logout Grok CLI riêng nếu cần.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xai_oauth",
        description="SuperGrok / xAI OAuth PKCE cho Discord bot Peto",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Đường dẫn file token (mặc định: .xai_tokens.json hoặc XAI_TOKEN_PATH)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Đăng nhập SuperGrok (mở browser)")
    p_login.add_argument(
        "--no-browser",
        action="store_true",
        help="Không tự mở browser — chỉ in URL",
    )
    p_login.set_defaults(func=_cmd_login)

    p_status = sub.add_parser("status", help="Kiểm tra token hiện có")
    p_status.set_defaults(func=_cmd_status)

    p_logout = sub.add_parser("logout", help="Xoá token project")
    p_logout.set_defaults(func=_cmd_logout)

    args = parser.parse_args(argv)
    # Propagate --path to subcommands
    if args.path is None and hasattr(args, "path"):
        pass
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
