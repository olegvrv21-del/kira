---
name: exe-dev-vm
description: Use when deploying HTTP services, exposing them via the exe.dev proxy, or working with disk-photon VM specifics (ports, public URLs, set-public).
---

This agent runs inside a sandboxed docker container with `/workspace` mounted. The host is `disk-photon.exe.xyz` (exe.dev VM, user `exedev` with passwordless sudo).

## Public URLs

HTTP services on **host ports 3000–9999** are reachable at:

```
https://disk-photon.exe.xyz:<port>/
```

Other ports are not proxied. Default to port 8000 for static demos.

The agent's container can't bind host ports directly. To run a public service:
1. Build artifacts in `/workspace`.
2. Tell the user to copy them out and run there, **or** install a systemd unit on the host.

## Static file serving (inside container)

```bash
busybox httpd -f -p 8000 -h .
```

## set-public

User may need to run `set-public <port>` once in their exe.dev shell to expose a port publicly. The agent typically cannot run this itself.

## Systemd on the host

For persistent services on the host (NOT in this sandbox), instruct the user:

```bash
sudo cp srv.service /etc/systemd/system/srv.service
sudo systemctl daemon-reload && sudo systemctl enable --now srv
```
