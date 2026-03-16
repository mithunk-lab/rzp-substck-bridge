# Bridge

Razorpay-to-Substack subscription sync for The Wire.

Automates the manual process of matching Razorpay payments to Substack subscribers and executing comp grants via browser automation.

## Architecture

| Component | Description |
|---|---|
| Razorpay webhook listener | Captures payments in real time |
| Identity resolution engine | Matches payer to Substack subscriber (exact email → fuzzy name → unknown) |
| Subscription state calculator | Computes correct comp duration based on amount and existing subscription |
| Substack executor | Executes the comp via Playwright browser automation |
| Operator dashboard | React frontend for exception handling only |

## Price → comp duration

| Amount (INR) | Comp |
|---|---|
| 200 | 30 days |
| 2,000 | 365 days |
| 10,000 | Lifetime |
| Other | Flagged for manual review |

## Setup

```bash
bash scripts/setup.sh
```

## Running locally

```bash
# Backend
cd backend && uvicorn main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

## Deployment

Hosted on Railway. Push to main triggers deployment. Migrations run automatically before the server starts (`alembic upgrade head && uvicorn ...`).
