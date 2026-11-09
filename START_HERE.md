# Start here

This page is the short version. You do **four things**, all of them in a web
browser. No terminal, no installing anything, and your own computer does not
need to be switched on.

When you are done, GitHub polls every job board **every 2 hours** and emails you
the new mechanical-engineering matches.

---

## What you will have afterwards

| | |
|---|---|
| Jobs collected automatically | Yes, every 2 hours, on GitHub's servers |
| Emails to your Gmail | Yes, only for new matches, never the same job twice |
| ITAR / US-citizen-only jobs | Removed entirely, you never see them |
| Your computer needs to be on | No |
| Cost | Free |

A clickable dashboard with filters is a separate, optional step (see
[the dashboard](#optional-the-clickable-dashboard) at the bottom) because it
needs somewhere to run. The email alerts do not.

---

## Step 1 — Create a Gmail App Password

Google will not let an app use your normal password, so you generate a
separate 16-character one.

1. Open **<https://myaccount.google.com/apppasswords>**
2. If it says App Passwords are unavailable, first turn on 2-Step Verification
   at <https://myaccount.google.com/signinoptions/two-step-verification>, then
   come back.
3. Type any name, for example `Job Tracker`, and press **Create**.
4. Copy the 16-character code it shows you, for example `abcd efgh ijkl mnop`.
   **Remove the spaces** when you paste it later: `abcdefghijklmnop`.

Keep that tab open, you need the code in the next step.

---

## Step 2 — Put your details into GitHub

1. Open **<https://github.com/RomelAhsan/MechEng-Job-Tracker/settings/secrets/actions>**
2. Press the green **New repository secret** button, top right.
3. Add these **four** secrets, one at a time. For each one: type the name in
   *Name*, paste the value in *Secret*, press **Add secret**, then press
   **New repository secret** again for the next one.

| Name (type exactly) | Value to paste |
|---|---|
| `NOTIFY_EMAIL` | your Gmail address, e.g. `you@gmail.com` |
| `SMTP_USERNAME` | the same Gmail address again |
| `SMTP_PASSWORD` | the 16-character App Password from Step 1, no spaces |
| `USER_PASSWORD` | any password you invent, for your own tracker account |

Names must match exactly, in capitals, with underscores. Once saved, GitHub
hides the values permanently, which is why they are safe here.

---

## Step 3 — Switch it on

1. Open **<https://github.com/RomelAhsan/MechEng-Job-Tracker/actions>**
2. If you see a button saying *I understand my workflows, go ahead and enable
   them*, press it.
3. In the left-hand list, click **Poll job boards**.
4. On the right, click **Run workflow**, then the green **Run workflow** button.

That starts the first collection immediately. It takes roughly 5–10 minutes
because it is reading every job board. After this first run it repeats on its
own every 2 hours, and you never touch it again.

---

## Step 4 — Check that it worked

Still on the **Actions** page, click the run that is going (or has a green
tick next to it).

- A **green tick** means it worked. New matching jobs were emailed to you.
  The first run usually sends no email, because on the very first pass every
  job is "already there" rather than new, which is exactly what stops you being
  spammed with thousands of old postings. From the second run onwards you get
  genuinely new jobs only.
- Scroll to the bottom of that page to **Artifacts** and download **jobs**.
  Inside are `jobs.md` and `jobs.csv`: the full current list of jobs, which you
  can open in any text editor or Excel. This is the quickest way to see
  everything without setting up a dashboard.
- A **red X** means something is wrong. Click the failed step to see why, and
  check [Troubleshooting](#troubleshooting).

---

## Optional — add AI analysis

The AI features (job analysis, tailored resumes) need a Google key. Without one
the rest of the app works normally and the AI pages just say "add a key".

1. Open **<https://aistudio.google.com/app/apikey>** and sign in.
2. Press **Create API key**. If it asks about a project, accept the default.
3. Copy the key. A real one starts with **`AIza`** and is about 39 characters.
4. Go back to
   **<https://github.com/RomelAhsan/MechEng-Job-Tracker/settings/secrets/actions>**,
   press **New repository secret**, name it `AI_API_KEY`, paste the key, save.

The key you sent me earlier (`AQ.Ab8RN6...`) was not an API key of this type and
returned "401 invalid", so please use a fresh `AIza...` one. Also delete that
old one, since it was shared in a chat.

---

## Optional — the clickable dashboard

The dashboard with filters, saved jobs and the pipeline is a small web app, so
it has to run somewhere. Two choices:

**On your own computer** — free, but only while it is running:

```bash
git clone https://github.com/RomelAhsan/MechEng-Job-Tracker.git
cd MechEng-Job-Tracker
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m jobtracker init-db && python -m jobtracker run && python -m jobtracker screen
python -m jobtracker dashboard
```

Then open <http://localhost:8501>.

**Hosted, with a permanent link** — follow [DEPLOY.md](DEPLOY.md) to put it on
Fly.io's free tier. That is the only way to get a web address that works from
your phone and keeps running by itself. It needs a Fly.io account, which only
you can create. Ask me and I will walk you through it one step at a time.

> Why the earlier link did not work: the dashboard was only ever running on a
> temporary machine. There is no permanent address until it is deployed.

---

## Troubleshooting

**No email arrived after the second run.**
Check the spelling of the four secret names in Step 2. Confirm `SMTP_PASSWORD`
is the 16-character App Password with the spaces removed, not your Google
password. Then open the latest Actions run, download the **jobs** artifact and
look in the `outbox` folder: if the emails are in there, the app is working but
the Gmail credentials were rejected.

**The "Ensure the notification account exists" step was skipped.**
`NOTIFY_EMAIL` or `USER_PASSWORD` is missing. Add it and run again.

**A red X on "Poll every enabled job board".**
Usually one job board was temporarily down. The app is built to carry on when a
single source fails, so just run it again. Persistent failures show up in the
`health` step output.

**It says I am out of Actions minutes.**
Private repositories get 2,000 free minutes a month, which is why this polls
every 2 hours instead of hourly. If you make the repository public, minutes
become unlimited and you can change `cron: "0 */2 * * *"` to `cron: "0 * * * *"`
in `.github/workflows/poll-jobs.yml` for hourly checks.

---

## Which file does what

| File | Purpose |
|---|---|
| `START_HERE.md` | This page |
| `README.md` | Full documentation of every feature and command |
| `DEPLOY.md` | Hosting the dashboard on Fly.io, backups |
| `.github/workflows/poll-jobs.yml` | The every-2-hours collector and emailer |
| `config/companies.yaml` | The list of employers being tracked |
| `config/user.yaml` | Your profile: term, duration, interests, citizenship |
