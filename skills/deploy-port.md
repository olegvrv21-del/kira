---
name: deploy-port
description: Use when the user asks to deploy/run a service on a specific port and get a public URL.
---

The sandbox container can't bind host ports directly, but `/workspace` is mounted on the host at `~/webchat/workspaces/<sid>/`. To make a service publicly reachable on the exe.dev proxy:

## Recipe

1. **Build inside the container.** Write the app (Python/Node/etc.) into `/workspace/<app>/`. Install deps in a venv inside the workspace.

2. **Test it inside the container** on `127.0.0.1:<port>` to make sure it starts and serves a healthz.

3. **Write a systemd unit** to `/workspace/<app>.service`:

```ini
[Unit]
Description=<app>
After=network.target

[Service]
Type=simple
User=exedev
WorkingDirectory=/home/exedev/webchat/workspaces/<sid>/<app>
EnvironmentFile=/home/exedev/webchat/workspaces/<sid>/<app>/.env
ExecStart=/home/exedev/webchat/workspaces/<sid>/<app>/.venv/bin/uvicorn app:app --host 0.0.0.0 --port <PORT>
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

4. **Instruct the user** to install it on the host:

```bash
sudo cp ~/webchat/workspaces/<sid>/<app>.service /etc/systemd/system/<app>.service
sudo systemctl daemon-reload
sudo systemctl enable --now <app>
```

5. **Public URL:** `https://disk-photon.exe.xyz:<PORT>/` (port must be 3000–9999).

## Constraints

- Ports outside 3000–9999 are NOT publicly reachable.
- Never put secrets in the unit file. Use `EnvironmentFile=`.
- Don't try to `systemctl` from inside the sandbox — the user must do it on the host.
- Don't bind to a port already used by another service. Check with `ss -tlnp` or ask the user.
