# Deploying MechEng-Job-Tracker

There are two ways to run this app. Pick based on what you need:

- **Local (recommended for a single user).** Runs on your own machine at
  `http://localhost:8501`. Nothing is hosted; the "link didn't work" earlier
  because it was only ever a local Streamlit server with no public URL. See
  [Run it locally](#run-it-locally).
- **Hosted on Fly.io (a real public HTTPS URL).** You deploy it to your own
  Fly.io account and get a live `https://<your-app>.fly.dev` URL with a
  background worker that polls job boards 24/7. See
  [Deploy to Fly.io](#deploy-to-flyio).

> **There is no permanent hosted URL provided for you.** This project cannot
> provision servers on your behalf, and the sandbox that generated the code
> cannot host it. A live URL exists only after **you** run the Fly.io steps
> below with **your own** Fly.io account (the free Hobby plan is enough).

---

## Run it locally

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m jobtracker init-db    # create/upgrade the SQLite database
python -m jobtracker run        # fetch jobs from all enabled companies
python -m jobtracker screen     # classify / score the jobs
python -m jobtracker dashboard  # opens http://localhost:8501
```

Leave the dashboard command running and open <http://localhost:8501> in your
browser. To keep jobs fresh automatically, run the scheduler in a second
terminal:

```bash
python -m jobtracker scheduler  # polls each board on its interval (<=1h)
```

---

## Deploy to Fly.io

Prerequisites: a (free) [Fly.io](https://fly.io) account and the `flyctl` CLI.

### 1. Install flyctl and log in

```bash
# macOS/Linux:
curl -L https://fly.io/install.sh | sh
# Windows (PowerShell):  iwr https://fly.io/install.sh -useb | iex

fly auth login
```

### 2. Create the app (without deploying yet)

From the repository root (the `fly.toml` and `Dockerfile` are already here):

```bash
fly launch --no-deploy
```

Accept the existing `fly.toml` when prompted, or let Fly pick a unique app
name and region. If it rewrites the file, keep the `[processes]`,
`[http_service]`, `[mounts]`, and `[deploy] release_command` sections.

### 3. Create the persistent volume for the SQLite database

The database lives on a Fly volume mounted at `/data` (see `fly.toml`), so it
survives restarts and deploys:

```bash
fly volumes create jobtracker_data --size 1 --region yyz
```

Use the same region as your app. 1 GB is plenty for SQLite.

### 4. Set your secrets

Secrets are injected as environment variables and never baked into the image:

```bash
# AI relevance engine (optional; omit to run without AI).
# Use a REAL Google AI Studio key (looks like "AIza...").
fly secrets set AI_PROVIDER=gemini AI_API_KEY=AIza_your_real_key AI_MODEL=gemini-1.5-flash

# Email notifications via Gmail (use a Gmail App Password, not your login).
fly secrets set \
  SMTP_HOST=smtp.gmail.com \
  SMTP_PORT=587 \
  SMTP_USE_TLS=true \
  SMTP_USERNAME=you@gmail.com \
  SMTP_PASSWORD=your_16_char_app_password \
  NOTIFY_FROM_EMAIL=you@gmail.com

```

> Authentication uses per-user accounts with bcrypt-hashed passwords (the first
> user to register becomes the admin). No separate auth/session secret is
> required. Register your account from the dashboard login page after the first
> deploy.

> Create a Gmail App Password at <https://myaccount.google.com/apppasswords>
> (2-Step Verification must be on). A token like `AQ.Ab8RN6...` is **not** a
> Gemini API key and will fail to authenticate — use an `AIza...` key.

### 5. Deploy

```bash
fly deploy
```

On deploy, the `release_command` runs `python -m jobtracker init-db` to apply
migrations against the volume database before the new release goes live.

### 6. Reach the live URL

```bash
fly open          # opens https://<your-app>.fly.dev in your browser
fly status        # shows the web + worker machines and the hostname
```

### 7. Scale the background worker

The `worker` process runs the polling scheduler. Make sure exactly one is
running (one is enough; more would double-poll):

```bash
fly scale count web=1 worker=1
fly logs -a <your-app>            # watch polling + notifications
```

---

## Backup and restore

The database is a single SQLite file, so backups are simple. Both commands read
the DB path from `DATABASE_URL`.

**Local:**

```bash
python -m jobtracker backup --output ./backups/jobtracker-$(date +%F).db
python -m jobtracker restore --input ./backups/jobtracker-2025-01-01.db
```

`backup` uses SQLite's online backup API, so it is safe to run while the app is
live. `restore` overwrites the current database — stop the app/scheduler first.

**On Fly.io**, you can either run the same commands via SSH into the machine, or
use Fly volume snapshots (automatic daily snapshots are retained; see
`fly volumes list` and `fly volumes snapshots list <volume-id>`):

```bash
fly ssh console -a <your-app>
# inside the machine:
python -m jobtracker backup --output /data/backup.db
```

---

## Updating a deployment

```bash
git pull           # or push your changes
fly deploy         # rebuilds the image, runs migrations, promotes the release
```
