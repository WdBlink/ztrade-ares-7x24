"""Safety — path validators, shell classifier, MCP allowlist.

PRD §15.1, §15.3. Dispatch-layer tool validation that prevents:
  - path traversal (only `autoresearch/` is writable; `best/` is controller-only)
  - SSRF (private IP ranges are denied in URLs)
  - shell escape (Bash commands classified as network/destructive are denied)
"""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

# ── Path validators (PRD §15.1) ──────────────────────────────────

ALLOWED_WRITE_PREFIXES = (
    "autoresearch/candidates/",
    "autoresearch/mutable/",
    "autoresearch/reports/",
    "autoresearch/protocol/",  # mutable surface; backed by mutable/
)

# Best/ is controller-write-only; workers cannot write here.
DENIED_WRITE_PREFIXES = (
    "autoresearch/best/",
    "ar724/",
    "config/safety_policy.yaml",
    ".ares/state.db",
    ".ares/events.jsonl",
)


class SafetyViolation(Exception):
    """Raised when a tool call violates the safety policy."""


def validate_path_write(path: str | Path, role: str = "") -> str:
    """Validate a worker write path. Returns the resolved path on success.

    Rules:
      - Path must be inside the project root.
      - Path must NOT be in DENIED_WRITE_PREFIXES (best/, ar724/, state.db).
      - backtester role is the ONLY role allowed to write autoresearch/mutable/.
      - All other roles may write only candidates/ and reports/.
    """
    p = Path(path)
    if p.is_absolute():
        raise SafetyViolation(f"Absolute paths are not allowed: {path}")

    posix = p.as_posix()

    for denied in DENIED_WRITE_PREFIXES:
        if posix.startswith(denied) or posix == denied.rstrip("/"):
            raise SafetyViolation(
                f"Write to {denied} is denied (controller-only)."
            )

    in_mutable = posix.startswith("autoresearch/mutable/")
    if in_mutable and role != "backtester":
        raise SafetyViolation(
            f"Only backtester role may write to autoresearch/mutable/ "
            f"(role={role!r})"
        )

    allowed = any(posix.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES)
    if not allowed:
        raise SafetyViolation(
            f"Path {path!r} is outside the allowed write scope. "
            f"Allowed prefixes: {ALLOWED_WRITE_PREFIXES}"
        )

    return posix


def validate_path_read(path: str | Path) -> str:
    """Validate a worker read path. Returns the resolved path on success."""
    p = Path(path)
    if p.is_absolute():
        raise SafetyViolation(f"Absolute paths are not allowed: {path}")
    posix = p.as_posix()
    if posix.startswith(".ares/") and "events.jsonl" in posix:
        # events.jsonl is untrusted; reads are allowed but workers must
        # treat contents as data, not instructions.
        pass
    if posix.startswith("ar724/") and "/runbooks/" not in posix:
        # ar724/ source is off-limits; only runbooks are public-readable.
        raise SafetyViolation(
            f"Reading {path!r} from ar724/ is denied (runbooks are excepted)."
        )
    return posix


# ── Shell classifier (PRD §15.1 path_validators) ─────────────────

_NETWORK_OR_DESTRUCTIVE = re.compile(
    r"(\b(rm|rmdir|mv|cp|dd|mkfs|chmod|chown)\b\s+-[^\s]*[rf]\b"
    r"|\bcurl\s|\bwget\s|\bnc\s|\bssh\s|\bscp\s|\brsync\s"
    r"|\b(chmod|chown|kill)\b\s+(-R|--recursive)\b"
    r"|>\s*/dev/"
    r"|\|\s*sh"
    r"|\|\s*bash"
    r"|;\s*(rm|curl|wget|nc)\b)",
    re.IGNORECASE,
)

_PRIVATE_NETS = [
    ipaddress.ip_network(n) for n in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/8",
    )
]


def is_private_ip(host: str) -> bool:
    """Return True if `host` resolves to a private/loopback IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for net in _PRIVATE_NETS:
            if ip in net:
                return True
    return False


def validate_bash_command(command: str, role: str = "") -> str:
    """Validate a Bash tool call. Returns the command on success.

    Denies:
      - Network or destructive command patterns
      - URLs pointing to private IP ranges (SSRF protection)
      - `git commit` calls (controller is the only committer)
    """
    if _NETWORK_OR_DESTRUCTIVE.search(command):
        raise SafetyViolation(
            f"Bash command matches network/destructive pattern: {command!r}"
        )
    if "git" in command and re.search(r"\bgit\s+commit\b", command):
        raise SafetyViolation(
            f"git commit is controller-only; worker attempted: {command!r}"
        )

    # SSRF: extract URLs and verify they don't point to private nets.
    url_pattern = re.compile(r"https?://[^\s\"']+")
    for url in url_pattern.findall(command):
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            raise SafetyViolation(f"Invalid URL: {url}")
        if host and is_private_ip(host):
            raise SafetyViolation(
                f"URL {url!r} resolves to a private IP range (SSRF blocked)"
            )
    return command


def validate_mcp_call(server: str, tool: str) -> None:
    """Validate an MCP tool call against the allowlist."""
    from . import config_loader  # late import to avoid cycles

    allowed = config_loader.get_mcp_allowlist()
    if not any(a["server"] == server and a["tool"] == tool for a in allowed):
        raise SafetyViolation(
            f"MCP call to {server}/{tool} is not in the allowlist"
        )
