#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       Bridge — First-Time Setup      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Step 1: copy .env.example → .env ──────────────────────────────────────────
if [ -f .env ]; then
  echo "⚠  .env already exists — skipping copy. Delete it and re-run to start fresh."
else
  cp .env.example .env
  echo "✓  Created .env from .env.example"
fi

echo ""
echo "You will now be prompted for each required variable."
echo "Press Enter to leave a variable blank and fill it in manually later."
echo ""

# Helper: prompt for a value and write it into .env
set_var() {
  local key="$1"
  local description="$2"
  local default="${3:-}"

  echo "▸ $key"
  echo "  $description"

  if [ -n "$default" ]; then
    read -r -p "  Value [${default}]: " value
    value="${value:-$default}"
  else
    read -r -p "  Value: " value
  fi

  if [ -n "$value" ]; then
    python3 - "$key" "$value" <<'PYEOF'
import sys, re
key, val = sys.argv[1], sys.argv[2]
with open('.env') as f:
    content = f.read()
content = re.sub(rf'^{re.escape(key)}=.*', f'{key}={val}', content, flags=re.MULTILINE)
with open('.env', 'w') as f:
    f.write(content)
PYEOF
  fi

  echo ""
}

# ── Step 2: prompt for each variable ──────────────────────────────────────────
set_var "DATABASE_URL" \
  "PostgreSQL connection string — Railway provides this (e.g. postgresql+asyncpg://user:pass@host:5432/db)"

set_var "RAZORPAY_WEBHOOK_SECRET" \
  "Webhook secret from Razorpay dashboard → Settings → Webhooks"

set_var "SUBSTACK_PUBLICATION_URL" \
  "Your Substack publication URL (e.g. https://thewire.substack.com)"

set_var "SUBSTACK_SESSION_COOKIE" \
  "Value of the substack.sid cookie — log in as editor, open DevTools → Application → Cookies"

set_var "SMTP_HOST" \
  "SMTP server hostname (e.g. smtp.gmail.com)"

set_var "SMTP_PORT" \
  "SMTP port — 587 for STARTTLS, 465 for SSL" "587"

set_var "SMTP_USER" \
  "SMTP login username (usually your email address)"

set_var "SMTP_PASSWORD" \
  "SMTP password or app-specific password"

set_var "CLARIFICATION_EMAIL_FROM" \
  "From address on subscriber clarification emails (e.g. subscriptions@thewire.in)"

set_var "DASHBOARD_API_KEY" \
  "Strong random string protecting the operator dashboard — generate one with: openssl rand -hex 32"

set_var "FRONTEND_URL" \
  "URL where the dashboard will be hosted (e.g. https://bridge.thewire.in)"

set_var "PLAYWRIGHT_HEADLESS" \
  "Run browser headless in production — set false to debug locally" "true"

set_var "DRY_RUN" \
  "When true, skips actual Substack changes and only logs what would happen" "false"

set_var "ENVIRONMENT" \
  "Deployment environment" "production"

# ── Step 3: install Python dependencies ───────────────────────────────────────
echo "Installing Python dependencies from backend/requirements.txt..."
pip install -r backend/requirements.txt
echo ""

# ── Step 4: run migrations ────────────────────────────────────────────────────
echo "Running database migrations..."
(cd backend && alembic upgrade head)
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════╗"
echo "║         Setup complete ✓             ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Start the backend:   cd backend && uvicorn main:app --reload"
echo "Start the frontend:  cd frontend && npm install && npm run dev"
echo ""
