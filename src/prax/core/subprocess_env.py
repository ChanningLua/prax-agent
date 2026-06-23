"""Environment for prax-spawned child processes (claude -p, verifiers).

prax runs on the host's main network stack. When a system VPN / clash TUN is
active there (the documented 7x24 setup: a Mac behind the GFW with Clash Verge
in TUN mode), the host's default route already reaches the whole internet —
Anthropic *and* otherwise-blocked hosts like jitpack.

The problem: an ``HTTP(S)_PROXY`` inherited from the launching shell/harness
diverts child HTTPS off that route onto the proxy. For this setup that proxy
sits inside China and GFW-blocks many hosts (jitpack, Sentry binaries, …), so
forwarding it makes children *worse* off than the host route. So by default we
STRIP proxy vars from child env, letting children inherit the host network stack
(= clash TUN). Same rationale as integrations/wechat_ilink (``trust_env=False``).

Escape hatch: set ``PRAX_KEEP_PROXY=1`` to keep the inherited proxy — for
deployments where the proxy is the ONLY egress (e.g. a headless server with no
VPN, where claude -p reaches Anthropic *through* the proxy).
"""
from __future__ import annotations

import os

_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def keep_proxy() -> bool:
    return os.environ.get("PRAX_KEEP_PROXY", "").lower() in ("1", "true", "yes")


def child_env(*, extra_pop: tuple[str, ...] = ()) -> dict[str, str]:
    """A copy of the current env for a spawned child.

    Pops ``extra_pop`` keys, and — unless ``PRAX_KEEP_PROXY`` is set — strips all
    proxy vars and forces ``NO_PROXY=*`` so the child goes direct over the host
    route (clash TUN) instead of the inherited proxy.
    """
    env = os.environ.copy()
    for key in extra_pop:
        env.pop(key, None)
    if not keep_proxy():
        for key in _PROXY_VARS:
            env.pop(key, None)
        # Belt-and-suspenders for libs that read a proxy from elsewhere: tell
        # them every host is direct.
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
    return env
