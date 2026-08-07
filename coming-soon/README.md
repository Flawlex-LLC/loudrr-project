# Loudrr — Coming Soon

Single-page Next.js 16 landing for the pre-launch window. WebGL audio-wave
background, Plus Jakarta + Syne typography, Tailwind v4. Designed to deploy
straight to Coolify as a Docker service.

## Local dev

```bash
npm install
npm run dev
# http://localhost:3000
```

## Production build (Docker)

```bash
docker build -t loudrr-coming-soon .
docker run --rm -p 3000:3000 loudrr-coming-soon
```

The Dockerfile uses Next's `output: 'standalone'` mode and runs as a
non-root user on port 3000.

## Coolify

Point Coolify at this repo + branch. Build pack: **Dockerfile**. Port: **3000**.
No env vars required. The included `HEALTHCHECK` hits `/` every 30s.

## Contact

Telegram support: [@ace_flawlex](https://t.me/ace_flawlex)
