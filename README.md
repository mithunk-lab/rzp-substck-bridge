# Bridge — Wire Subscription Sync

## 1. What This Is

Bridge is an internal tool built for The Wire that automates the subscription fulfillment workflow. When a reader pays via Razorpay, Bridge receives the payment in real time, identifies the corresponding Substack subscriber, calculates how many days of access they are owed, and executes the comp grant on Substack automatically — without any manual intervention in the normal case. Payments that cannot be matched automatically are surfaced in an operator dashboard where an editor can review and approve them. Bridge exists because Razorpay and Substack have no native integration, and the manual process of matching payments to subscribers and executing comps was error-prone and time-consuming.

---

## 2. Architecture

Bridge has five components that work in sequence:

**Razorpay webhook listener** — Razorpay sends a notification to Bridge the moment a payment is captured. Bridge verifies the signature, stores the payment record, and hands it off to the identity engine.

**Identity resolution engine** — Tries to find the matching Substack subscriber for the payment. It works in three tiers: first it tries an exact email match, then a fuzzy name match if the email fails, and if both fail it sends the payer a clarification email and flags the payment as unknown.

**Subscription state calculator** — Once a subscriber is identified, this component works out the correct comp duration. It accounts for the payment amount (₹200 = 30 days, ₹2,000 = 365 days, ₹10,000 = lifetime) and the subscriber's existing expiry date — if they have time remaining, it adds to it rather than overwriting.

**Substack executor** — Uses a headless browser (Playwright) to log into the Substack dashboard and execute the comp grant. It takes a screenshot as proof and stores the result. If anything goes wrong it fails cleanly and surfaces the failure for manual retry.

**Operator dashboard** — A React web interface used only for exceptions: payments that couldn't be matched automatically, failed executor runs, and subscriber sync management. In normal operation an editor spends under five minutes a day here.

---

## 3. First-Time Setup (Railway)

### a. Clone the repository

```bash
git clone https://github.com/mithunk-lab/rzp-substck-bridge.git
cd rzp-substck-bridge
```

### b. Create a Railway project

Go to [railway.app](https://railway.app), create a new project, and connect it to your GitHub repository.

### c. Add a PostgreSQL database service

Inside your Railway project, click **New** → **Database** → **PostgreSQL**. Railway will provision the database and automatically make `DATABASE_URL` available to your backend service.

### d. Add the backend service

Click **New** → **GitHub Repo** → select this repository. Set the root directory to `/backend`. Then set the following environment variables in the Railway service settings:

| Variable | What it is | Where to find it |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Set automatically by the Railway Postgres plugin |
| `RAZORPAY_WEBHOOK_SECRET` | The secret used to verify incoming Razorpay webhook signatures | Razorpay Dashboard → Webhooks → your webhook → Secret |
| `SUBSTACK_PUBLICATION_URL` | The full URL of your Substack publication | e.g. `https://thewire.substack.com` |
| `SUBSTACK_SESSION_COOKIE` | The session cookie value for the Substack account that manages comps | See Section 6 for how to obtain this |
| `DASHBOARD_API_KEY` | A secret key that protects the operator dashboard | Generate a strong random string, e.g. `openssl rand -hex 32` |
| `FRONTEND_URL` | The URL of the deployed frontend service | Your Railway frontend domain, e.g. `https://bridge-frontend.up.railway.app` |
| `ENVIRONMENT` | Controls scheduler and debug behaviour | Set to `production` |
| `SMTP_HOST` | Mail server hostname for clarification emails | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | Mail server port | Usually `587` |
| `SMTP_USER` | Email account username | The sending email address |
| `SMTP_PASSWORD` | Email account password or app password | From your email provider |
| `CLARIFICATION_EMAIL_FROM` | The From address shown on clarification emails | e.g. `subscriptions@thewire.in` |
| `PLAYWRIGHT_HEADLESS` | Whether the browser runs headless | Set to `true` in production |
| `DRY_RUN` | If true, the executor stops before clicking confirm | Set to `false` in production; use `true` for initial testing |

### e. Add the frontend service

Click **New** → **GitHub Repo** → same repository. Set the root directory to `/frontend`. Add one environment variable:

| Variable | What it is |
|---|---|
| `VITE_API_URL` | The full URL of your deployed backend service, e.g. `https://bridge-backend.up.railway.app` |

### f. Deploy both services

Push to `main` or trigger a manual deploy from the Railway dashboard. The backend runs `alembic upgrade head` automatically before starting — this creates all database tables on first deploy.

### g. Verify the health endpoint

Once deployed, visit:

```
https://your-backend-domain.up.railway.app/health
```

You should receive:

```json
{"status": "ok", "timestamp": "...", "environment": "production"}
```

### h. Upload your first Substack CSV

Open the dashboard at your frontend URL, enter your `DASHBOARD_API_KEY`, navigate to **SETTINGS**, and upload a Substack subscriber CSV. This populates the local subscriber cache that the identity engine uses for matching. See Section 5 for how to export the CSV from Substack.

### i. Configure the Razorpay webhook

See Section 4.

---

## 4. Razorpay Webhook Setup

1. Log into the [Razorpay Dashboard](https://dashboard.razorpay.com).
2. Go to **Settings** → **Webhooks** → **Add New Webhook**.
3. Set the **Webhook URL** to:
   ```
   https://your-backend-domain.up.railway.app/webhooks/razorpay
   ```
4. Under **Events**, enable **`payment.captured`** only. Do not enable other events.
5. Set a **Secret** — use any strong random string. Copy it immediately.
6. Go to your Railway project → backend service → **Variables** → set `RAZORPAY_WEBHOOK_SECRET` to the value you just copied.
7. Save the webhook. Razorpay will send a test ping — Bridge will receive it and return 200.

---

## 5. Substack CSV Export

Bridge uses a local cache of your Substack subscriber list for identity matching. Keep this cache up to date by uploading a fresh CSV regularly. Recommended frequency: daily.

**To export from Substack:**

1. Log into your Substack publication dashboard.
2. Go to **Subscribers** in the left sidebar.
3. Click **Export** in the top right of the subscriber list.
4. Substack will email you a download link, or the CSV will download directly.
5. The CSV should include columns: `email`, `name`, `subscription_status`, `expiry_date`.

**To upload to Bridge:**

1. Open the operator dashboard and navigate to **SETTINGS**.
2. Under **Subscriber Sync**, click **UPLOAD SUBSCRIBER CSV**.
3. Select the file. Bridge will process it and show a summary: records processed, inserted, updated, and marked deleted.

---

## 6. Refreshing the Substack Session Cookie

Bridge uses a Substack session cookie to authenticate the headless browser that executes comp grants. This cookie expires periodically (typically every few weeks). When it expires, the dashboard will show a red **COOKIE: EXPIRED** warning and all executor runs will fail.

**To refresh the cookie:**

1. Open Google Chrome and navigate to your Substack publication dashboard. Make sure you are logged in.
2. Open **Developer Tools** — press `F12` on Windows/Linux or `Cmd+Option+I` on Mac.
3. Click the **Application** tab at the top of Developer Tools (click the `»` arrow if it is not visible).
4. In the left sidebar, expand **Cookies** and click on `https://substack.com`.
5. In the cookie list, find the cookie named **`substack.sid`**. This is the session cookie.
6. Click on it and copy the entire string from the **Value** column.
7. Go to your Railway project → backend service → **Variables**.
8. Find `SUBSTACK_SESSION_COOKIE` and replace its value with what you just copied.
9. Click **Deploy** to redeploy the backend with the new cookie.
10. Open the operator dashboard → **SETTINGS** → **Substack Connection**. The status should now show **OK**.

---

## 7. Daily Operator Workflow

In normal operation Bridge runs fully automatically. The editor's daily tasks are:

**Check the Inbox (2 minutes)**
Open the dashboard. If **PENDING** shows a number greater than zero, there are payments that couldn't be matched automatically. For each one, either confirm the suggested match or use the Override button to search for the correct subscriber manually.

**Sync subscribers if overdue (1 minute)**
If the **SYNC** indicator shows **OVERDUE**, export a fresh CSV from Substack and upload it via Settings. This means it has been more than 24 hours since the last upload.

**Check Failed actions (1 minute)**
If **FAILED** shows a number greater than zero, open the Failed view. Read the failure reason for each. Most failures are either a cookie expiry (follow Section 6) or a subscriber not found on Substack (the email in Razorpay doesn't match what's listed on Substack — use Override in the Inbox to manually select the correct subscriber). Click Retry once the underlying issue is resolved.

**Estimated time when running normally: under 5 minutes.**

---

## 8. Data and Privacy

> ⚠️ **NOT YET IMPLEMENTED** — The automated data hygiene jobs described in this section (phone number auto-nullification and scheduled deletion reconciliation) have been designed and documented but not yet built. The data storage behaviour described below is accurate. The automatic deletion schedule is planned for a future release and will be added to this codebase without any changes to the existing architecture.

**What data Bridge stores:**

Bridge stores three categories of data in its PostgreSQL database:

- **Payment records** — Razorpay payment ID, payer name, email, phone number, amount, and timestamp. These are stored to support identity resolution and provide an audit trail of all payments received.
- **Subscriber cache** — A local copy of your Substack subscriber list including email, name, subscription status, and expiry date. This is used for matching and is updated each time you upload a CSV. It is not the source of truth — Substack is.
- **Action records** — A log of every comp grant attempted, including the outcome, comp duration granted, and a screenshot of the Substack dashboard at the time of execution. This is the audit trail for subscription fulfillment.

**Phone number retention (planned):**

Phone numbers are collected from Razorpay payment data solely to assist with identity resolution in cases where name and email matching fails. Once a payment reaches `completed` or `auto_resolved` status and is more than 30 days old, the phone number field is intended to be automatically set to null. This fulfils the data minimisation principle under the Digital Personal Data Protection Act 2023 (DPDPA). Until this job is implemented, phone numbers will persist in the database indefinitely.

**Subscriber cache:**

The subscriber cache reflects your Substack subscriber list at the time of the last CSV upload. When a subscriber is removed from Substack and the next CSV is uploaded, Bridge marks them as `deleted_from_substack` rather than deleting the record — this preserves the audit trail for any past payments associated with that subscriber.

**DPDPA 2023:**

Bridge is designed with DPDPA compliance in mind. Payment data is collected solely for the purpose of subscription fulfillment, access is restricted to authorised operators via API key authentication, and the planned phone number nullification schedule directly addresses the Act's data minimisation requirement.

---

## 9. Troubleshooting

**COOKIE: EXPIRED warning in the dashboard**

The Substack session cookie has expired. Follow Section 6 to refresh it. All pending executor actions will need to be retried after the new cookie is deployed.

**SYNC: OVERDUE warning in the dashboard**

No subscriber CSV has been uploaded in the last 24 hours. Export a fresh CSV from Substack and upload it via Settings → Subscriber Sync.

**Executor failed with "Subscriber email not found on Substack dashboard"**

The subscriber's email address recorded in the Razorpay payment does not match what is listed on Substack. Go to the Inbox, find the payment, and use the **OVERRIDE** button to manually search for and select the correct subscriber record. Then retry the action from the Failed view.

**SMTP errors / clarification emails not sending**

Check that `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, and `SMTP_PASSWORD` are all set correctly in Railway. If using Gmail, you must use an App Password rather than your regular account password — generate one at Google Account → Security → 2-Step Verification → App passwords.
