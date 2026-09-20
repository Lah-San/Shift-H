# Deploying Shift-H

## Hugging Face Spaces (free, no card)

A Docker Space builds the `Dockerfile` in this repository and serves it on an HTTPS URL. Free hardware is 2 vCPU and 16 GB RAM, more than the 1.3 GB the app needs.

### One-time setup (in the browser)

1. Sign in at huggingface.co and open **New Space**.
2. Name it (for example `shift-h`), choose **Docker** as the SDK with the **Blank** template, pick **Public** (the app has its own sign-in) or **Private** (only you can open it), and create it.
3. In the Space, open **Settings → Variables and secrets** and add these **secrets**:
   - `GEMINI_API_KEY` — a fresh Google AI Studio key
   - `ADMIN_PASSWORD`, `MANAGER_PASSWORD`, `STAFF_PASSWORD` — the demo account passwords
4. Create a **write** access token at huggingface.co/settings/tokens (used once, as the git password).

### Push the app (from this folder)

```bash
git remote add hf https://huggingface.co/spaces/YOUR_USER/shift-h
git push --force hf main          # username = your HF user name, password = the write token
```

The Space builds for two to three minutes, then shows the app at `https://YOUR_USER-shift-h.hf.space`. Every later `git push hf main` redeploys.

### Good to know

- The first request after a build or after the Space wakes from sleep takes about 10 s while the roster loads.
- A free Space sleeps after 48 hours without visitors and wakes on the next visit. Open it once before the demo.
- The request database is inside the container: it resets on every rebuild or restart. That gives a clean inbox for a demo; persistent disks are a paid add-on.
- Secrets never enter the image; they are injected as environment variables at run time.

## Any Docker host

```bash
docker build -t shift-h .
docker run -p 8080:8080 -e GEMINI_API_KEY=... -e ADMIN_PASSWORD=... -e MANAGER_PASSWORD=... -e STAFF_PASSWORD=... shift-h
```

## Local demo through a tunnel (backup)

If hosting fails on the day, run `python run.py` locally and expose it with a free tunnel such as `cloudflared tunnel --url http://127.0.0.1:8000`; the printed URL works for anyone for as long as the laptop is up.
