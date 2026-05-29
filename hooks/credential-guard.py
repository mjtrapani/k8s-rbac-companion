#!/usr/bin/env python3
"""credential-guard — PreToolUse hook for the k8s-rbac-companion plugin.

Reads a Claude Code hook payload from stdin, scans Write/Edit/MultiEdit content
(and Bash command strings) for literal Kubernetes credentials (ServiceAccount/
OIDC bearer JWTs, kubeconfig token: fields, embedded client-key-data /
client-certificate-data blobs), and blocks the write/command if one is found.

Recognized placeholders (e.g., <changeme>, ${VAR}, $SA_TOKEN, ALL_CAPS names)
are allowed through — they're how the agent's own output is structured.
"""
import json
import re
import sys

PATTERNS = [
    (
        # ServiceAccount / OIDC bearer JWT: a three-segment base64url token
        # whose header begins with `eyJ` (base64 of `{"`). No capture group —
        # scan() falls back to match.group(0) (the whole token). Each segment
        # is required to be non-trivial in length to avoid false positives on
        # short eyJ-prefixed strings.
        re.compile(r"\beyJ[A-Za-z0-9_=-]{10,}\.[A-Za-z0-9_=-]{10,}\.[A-Za-z0-9_=-]{6,}"),
        "ServiceAccount / OIDC bearer JWT (eyJ... header.payload.signature)",
    ),
    (
        # kubeconfig `token:` YAML field assigned a literal bearer token.
        # Capture group 1 = the value (so placeholders like ${VAR}/<changeme>
        # are caught by is_placeholder()).
        re.compile(r"(?im)^\s*token:\s*[\"']?([^\s\"'#]+)"),
        "kubeconfig token: field with literal bearer token",
    ),
    (
        # kubeconfig client-key-data / client-certificate-data: a base64-encoded
        # private key or client certificate blob. The key name is a NON-capturing
        # alternation so group 1 stays the value. Require a long blob (40+ base64
        # chars) to avoid matching short placeholders. NOTE: deliberately does NOT
        # match certificate-authority-data — that's the public CA cert, not a
        # secret, and blocking it would be wrong.
        re.compile(
            r"(?im)^\s*(?:client-key-data|client-certificate-data):\s*[\"']?([A-Za-z0-9+/=_-]{40,})"
        ),
        "kubeconfig client-key-data / client-certificate-data (base64 private key / client cert)",
    ),
]

PLACEHOLDERS = {
    "changeme",
    "replace_with_password",
    "replace_password",
    "your_password",
    "your-password",
    "your_password_here",
    "changeme",
    "change_me",
    "change-me",
    "password",
    "secret",
    "xxx",
    "xxxx",
    "xxxxx",
    "placeholder",
    "none",
    "null",
    "redacted",
    "example",
}


def is_placeholder(value: str) -> bool:
    """Heuristic: is this value an obvious placeholder, not a real credential?"""
    raw = value.strip().strip("\"'")
    v = raw.lower()
    if not v:
        return True
    if v in PLACEHOLDERS:
        return True
    if v.startswith("<") and v.endswith(">"):
        return True
    if v.startswith("${") and v.endswith("}"):
        return True
    if re.fullmatch(r"\$[a-z_][a-z0-9_]*", v):
        return True
    # ALL_CAPS_WITH_UNDERSCORES form: classic env-var-style placeholder
    # used in instructional examples — YOUR_SA_TOKEN, KUBE_TOKEN,
    # MY_SECRET_TOKEN, etc. Conservative trade-off: a real token that
    # happens to be all uppercase letters/numbers/underscores will pass
    # through (we accept that — high-entropy real secrets are mixed-case
    # with special chars, and the README documents this limitation
    # explicitly under "What the hook doesn't cover").
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw):
        return True
    return False


def line_of(content: str, idx: int) -> str:
    start = content.rfind("\n", 0, idx) + 1
    end = content.find("\n", idx)
    return content[start : end if end != -1 else len(content)].strip()


def scan(content: str) -> list[str]:
    findings = []
    for pattern, label in PATTERNS:
        for match in pattern.finditer(content):
            value = match.group(1) if match.lastindex else match.group(0)
            if is_placeholder(value):
                continue
            line = line_of(content, match.start())
            findings.append(f"  - {label}\n      line: {line}")
    return findings


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Fail open on malformed input — better than blocking everything.

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    contents = []
    if tool_name == "Write":
        contents.append(tool_input.get("content", ""))
    elif tool_name == "Edit":
        contents.append(tool_input.get("new_string", ""))
    elif tool_name == "MultiEdit":
        for edit in tool_input.get("edits", []):
            contents.append(edit.get("new_string", ""))
    elif tool_name == "Bash":
        # Heredocs/redirects (`cat > kubeconfig <<EOF ... token: ... EOF`) and
        # inline credential flags (`--token=eyJ...`) write secrets to disk
        # without touching the Write/Edit primitives. Best-effort: the two
        # YAML-anchored patterns fire on heredoc-body lines; the JWT pattern
        # catches inline tokens. Opaque non-JWT bearer tokens passed inline are
        # not caught (documented in the README).
        contents.append(tool_input.get("command", ""))
    else:
        return 0

    findings = []
    for content in contents:
        if content:
            findings.extend(scan(content))

    if not findings:
        return 0

    if tool_name == "Bash":
        target = "run this Bash command"
    else:
        target = f"write {tool_input.get('file_path', '(unknown path)')}"
    sys.stderr.write(
        f"\ncredential-guard: refused to {target}\n\n"
        f"Detected literal Kubernetes credential(s) in the content:\n\n"
        + "\n".join(findings)
        + "\n\nBest practice: load Kubernetes credentials from a kubeconfig "
        "referenced by $KUBECONFIG, projected ServiceAccount tokens, or a secret "
        "manager at runtime. Don't commit literal tokens or key/cert data.\n\n"
        "If this is intentional (e.g., a test fixture or local-dev override), "
        "use a recognized placeholder — <changeme>, ${SA_TOKEN}, $SA_TOKEN, "
        "ALL_CAPS names — or temporarily disable the k8s-rbac-companion plugin.\n"
    )
    return 2  # Non-zero exit + stderr → Claude Code blocks the tool call.


if __name__ == "__main__":
    sys.exit(main())
