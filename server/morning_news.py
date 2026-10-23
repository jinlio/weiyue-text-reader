# -*- coding: utf-8 -*-
"""薇阅云端早间新闻生成器。

每天从公开 RSS 新闻源抓取新闻，整理成当天的早间新闻文件，
输出到网站根目录的 早间新闻/ 文件夹（文件名：早间新闻-YYYY-MM-DD.txt）。
薇阅电脑版配置好服务器地址后，启动时会自动拉取这份文件并提示朗读。

也可以由你自己的 AI agent / 脚本接管：直接把当天新闻写成
早间新闻/早间新闻-YYYY-MM-DD.txt 即可，薇阅同样能读到。
"""
import html
import ipaddress
import os
import re
import socket
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from defusedxml import ElementTree as ET

WEB_ROOT = os.environ.get('WY_WEB_ROOT', '/var/www/html')
OUT_DIR = os.path.join(WEB_ROOT, '早间新闻')
MAX_ITEMS = 15
READ_TIMEOUT = 20

# 公开 RSS 新闻源（任选可用即可，全部失败会提示）
SOURCES = [
    'https://feeds.bbci.co.uk/zhongwen/simp/rss.xml',          # BBC 中文
    'https://www.zaobao.com.sg/rss',                            # 联合早报
    'https://www.cna.com.tw/rss/rssall.aspx',                   # 中央社
    'https://rsshub.app/zhihu/daily',                           # 知乎日报（若可用）
]

ALLOWED_HOSTS = frozenset(
    urllib.parse.urlsplit(u).hostname for u in SOURCES)


def _validate_url(url):
    """只允许 http/https 和 SOURCES 里的公开主机，阻断内网/保留地址。"""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError as e:
        raise ValueError('地址格式无效：%s' % e)
    if parts.scheme not in ('http', 'https'):
        raise ValueError('仅允许 http/https 地址：%s' % url)
    host = (parts.hostname or '').rstrip('.').lower()
    if not host or host not in ALLOWED_HOSTS:
        raise ValueError('不允许访问的主机：%s' % host)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise ValueError('无法解析主机 %s：%s' % (host, e))
    for info in infos:
        addr = info[4][0].split('%', 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise ValueError('无法识别的地址：%s' % addr)
        if not ip.is_global:
            raise ValueError('拒绝访问本机/内网/保留地址：%s' % ip)


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    _validate_url(url)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'WeiyueMorningNews/1.0 (+https://github.com/)'
    })
    opener = urllib.request.build_opener(_RedirectGuard())
    with opener.open(req, timeout=READ_TIMEOUT) as r:
        return r.read(5 * 1024 * 1024)


def clean(text):
    text = html.unescape(text or '')
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_rss(data):
    items = []
    root = ET.fromstring(data)
    for item in root.iter('item'):
        title = clean(item.findtext('title'))
        desc = clean(item.findtext('description'))
        link = clean(item.findtext('link'))
        if title:
            items.append((title, desc, link))
    return items


def main():
    today = time.strftime('%Y-%m-%d')
    all_items = []
    used = set()
    for url in SOURCES:
        try:
            for title, desc, link in parse_rss(fetch(url)):
                if title in used:
                    continue
                used.add(title)
                all_items.append((title, desc, link))
        except Exception as e:
            print('来源不可用：%s（%s）' % (url, e))
        if len(all_items) >= MAX_ITEMS:
            break
    if not all_items:
        print('所有新闻源都失败了，今天没有生成早间新闻。')
        return 1

    lines = ['早间新闻 · %s' % today, '']
    for i, (title, desc, link) in enumerate(all_items[:MAX_ITEMS], 1):
        lines.append('%d. %s' % (i, title))
        if desc and desc != title:
            lines.append('   %s' % desc[:80])
        if link:
            lines.append('   %s' % link)
        lines.append('')

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, '早间新闻-%s.txt' % today)
    fd, tmp = tempfile.mkstemp(prefix='.morning_', suffix='.txt', dir=OUT_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            fd = None
            f.write('\n'.join(lines))
        os.replace(tmp, out)
        tmp = None
    finally:
        if fd is not None:
            os.close(fd)
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
    print('已生成：%s（%d 条）' % (out, len(all_items)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
