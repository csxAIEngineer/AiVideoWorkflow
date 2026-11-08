"""Fail closed if a test reaches a provider call or a non-local socket."""

import urllib.parse
import urllib.request
from unittest.mock import patch

_PATCHES = []
_URL_OPEN = urllib.request.urlopen


def _boom(*_args, **_kwargs):
    raise AssertionError("offline guard: 禁止真实模型调用")


def _urlopen(request, *args, **kwargs):
    url = getattr(request, "full_url", None) or str(request)
    host = urllib.parse.urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", None):
        raise AssertionError("offline guard: 禁止访问 %s" % host)
    return _URL_OPEN(request, *args, **kwargs)


def install():
    if _PATCHES:
        return
    targets = (
        patch("end.ai_client.ask_text", _boom),
        patch("urllib.request.urlopen", _urlopen),
    )
    for item in targets:
        item.start()
        _PATCHES.append(item)


def uninstall():
    while _PATCHES:
        _PATCHES.pop().stop()
