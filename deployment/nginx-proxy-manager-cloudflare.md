# Cloudflare + Nginx Proxy Manager

This app is ready to sit behind Cloudflare and Nginx Proxy Manager.

## App process

- Run the FastAPI app locally on the server with `systemd`
- Bind Uvicorn to `127.0.0.1:8000`
- Do not expose Uvicorn directly to the public internet

## Suggested layout

- App code: `/opt/muuc-upload-portal`
- Virtualenv: `/opt/muuc-upload-portal/.venv`
- Env file: `/opt/muuc-upload-portal/.env`
- Storage root from `.env`: `/srv/muuc-upload-portal/data`

## Nginx Proxy Manager host

Create a Proxy Host in Nginx Proxy Manager with:

- Domain Names: your upload portal hostname
- Scheme: `http`
- Forward Hostname/IP: `127.0.0.1`
- Forward Port: `8000`
- Websockets Support: enabled
- Block Common Exploits: enabled
- Cache Assets: optional

## SSL

If Cloudflare is in front:

- Use Cloudflare DNS for the hostname
- Enable SSL in Nginx Proxy Manager for that host
- Prefer Cloudflare SSL mode `Full (strict)`

## Cloudflare

- Point the DNS record to the server running Nginx Proxy Manager
- Keep the orange cloud enabled if you want Cloudflare proxying
- If upload sizes are large, confirm your Cloudflare plan limits are acceptable

## systemd

Copy `deployment/muuc-upload-portal.service` to:

- `/etc/systemd/system/muuc-upload-portal.service`

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable muuc-upload-portal
sudo systemctl start muuc-upload-portal
sudo systemctl status muuc-upload-portal
```
