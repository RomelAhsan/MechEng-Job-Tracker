# MechEng-Job-Tracker production image (SPEC Phase 5).
#
# Deliberately light: python:3.11-slim plus pure-Python/wheels-only deps
# (reportlab for PDF, stdlib smtplib for email) so there is NO need for native
# build toolchains or system libraries. The same image serves both Fly.io
# processes defined in fly.toml:
#   * web    -> streamlit run jobtracker/dashboard/app.py (0.0.0.0:8080)
#   * worker -> python -m jobtracker scheduler
FROM python:3.11-slim

# Keep Python output unbuffered and skip .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy project metadata first so dependency installation is cached across
# source-only changes.
COPY pyproject.toml README.md alembic.ini ./

# Copy the application source, Alembic migrations, and seed config. These live
# alongside the package so `python -m jobtracker init-db` (which resolves
# alembic.ini + migrations relative to the package root) and companies.yaml /
# user.yaml loading work at runtime.
COPY jobtracker ./jobtracker
COPY migrations ./migrations
COPY config ./config

# Editable install so `Path(__file__).parent.parent` inside the package resolves
# to /app, where alembic.ini and migrations/ live.
RUN pip install --upgrade pip && pip install -e .

# Default DB location points at the Fly volume mount (see fly.toml). Override
# locally with -e DATABASE_URL=... if desired.
ENV DATABASE_URL=sqlite:////data/jobtracker.db

# Streamlit must bind the Fly-provided port on all interfaces.
EXPOSE 8080

# Default command is the web dashboard; fly.toml's [processes] overrides this
# per-process (web = streamlit, worker = scheduler).
CMD ["streamlit", "run", "jobtracker/dashboard/app.py", \
     "--server.port", "8080", "--server.address", "0.0.0.0"]
