# Reading Sound Game Deployment

This deployment runs the FastAPI backend and static web app in one app container,
with Caddy as the public reverse proxy.

## Local container run

Copy the sample environment file and fill in your Azure Speech values:

```powershell
Copy-Item .env.example .env
```

Then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080` if `HTTP_PORT=8080` is set in `.env`.

## AWS notes

The browser microphone API requires a secure context. `localhost` works for local
testing, but the deployed site needs HTTPS for speech-to-text in normal browsers.
Use a real domain for `SITE_ADDRESS` so Caddy can request and renew a certificate.

If you set `SITE_ADDRESS=:80`, the site can load over plain HTTP, but microphone
recording will not work in production browsers.
