# -*- coding: utf-8 -*-
"""出站请求、本地路径与 XML 解析的安全边界。

所有联网请求先校验协议、主机名和解析后的 IP，阻断本机/内网/保留地址；
写入配置或书库文件前确认目标仍在允许目录内；解析外部 XML 前拒绝 DTD/实体声明。
"""
import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.request


class UnsafeURLError(ValueError):
    pass


class UnsafePathError(ValueError):
    pass


class UnsafeXMLError(ValueError):
    pass


def _validate_url(url, allowed_hosts=None):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError as e:
        raise UnsafeURLError('地址格式无效：%s' % e)
    if parts.scheme not in ('http', 'https'):
        raise UnsafeURLError('仅允许 http/https 地址：%s' % url)
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        raise UnsafeURLError('地址缺少主机名：%s' % url)
    if allowed_hosts:
        if not any(host == h or host.endswith('.' + h)
                   for h in allowed_hosts):
            raise UnsafeURLError('不允许访问的主机：%s' % host)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeURLError('无法解析主机 %s：%s' % (host, e))
    if not infos:
        raise UnsafeURLError('无法解析主机：%s' % host)
    for info in infos:
        addr = info[4][0].split('%', 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise UnsafeURLError('无法识别的地址：%s' % addr)
        if not ip.is_global:
            raise UnsafeURLError('拒绝访问本机/内网/保留地址：%s' % ip)


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    """每一次重定向都重新做同样的协议/主机/IP 校验。"""

    def __init__(self, allowed_hosts=None, max_hops=5):
        super().__init__()
        self._allowed_hosts = allowed_hosts
        self.max_redirections = max_hops

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl, self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_read(target, timeout=15, allowed_hosts=None, max_bytes=8 * 1024 * 1024):
    """校验 URL 后请求，最多读取 max_bytes 字节并返回 bytes。"""
    url = (target.full_url if isinstance(target, urllib.request.Request)
           else str(target))
    _validate_url(url, allowed_hosts)
    opener = urllib.request.build_opener(_RedirectGuard(allowed_hosts))
    with opener.open(target, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeURLError('响应超过 %d 字节，已中止：%s' % (max_bytes, url))
    return data


def ensure_within(base, path):
    """确认 path 规范化后仍位于 base 目录内，返回规范化路径。"""
    base_real = os.path.realpath(base)
    path_real = os.path.realpath(path)
    try:
        common = os.path.commonpath([base_real, path_real])
    except ValueError:
        raise UnsafePathError('路径不在允许目录内：%s' % path)
    if os.path.normcase(common) != os.path.normcase(base_real):
        raise UnsafePathError('路径不在允许目录内：%s' % path)
    return path_real


_XML_DTD_RE = re.compile(rb'<!\s*(DOCTYPE|ENTITY)', re.IGNORECASE)


def safe_xml_fromstring(data, max_bytes=64 * 1024 * 1024):
    """拒绝 DTD/实体声明并限制大小后用 defusedxml 解析。"""
    try:
        from defusedxml import ElementTree as SafeET
    except ImportError:
        raise UnsafeXMLError('缺少 defusedxml，无法安全解析外部 XML')
    raw = (data.encode('utf-8', 'ignore') if isinstance(data, str)
           else bytes(data))
    if len(raw) > max_bytes:
        raise UnsafeXMLError('XML 超过 %d 字节，已拒绝' % max_bytes)
    if _XML_DTD_RE.search(raw):
        raise UnsafeXMLError('XML 包含 DTD/实体声明，已拒绝')
    return SafeET.fromstring(data)