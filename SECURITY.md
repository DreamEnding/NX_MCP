# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository's Security tab
when available. If that option is unavailable, ask the maintainer for a private
reporting channel without publishing exploit details or secrets in an issue.

Include the affected commit/version, bridge, NX build, prerequisites, expected
and observed behavior, and a minimal reproducer using disposable native parts.
Remove tokens, descriptors, licence data and confidential paths or CAD. The
maintainer will coordinate investigation and disclosure through that channel.

## Scope and supported versions

The maintained development line is `0.2.0.dev0`; it has not met the `0.2.0`
release acceptance gate. The legacy `0.1` tool surface is unverified and has no
certification claim. Report findings against the current default branch.

The bridge is intended for a single user's local native NX session. Its boundary
is authenticated loopback JSON-RPC, per-session tokens and a confined workspace.
A readable descriptor grants bridge access; treat it as a credential. Remote
network exposure and Teamcenter-managed sessions are outside the current scope.
Experimental tools and arbitrary Journals remain disabled by default.
