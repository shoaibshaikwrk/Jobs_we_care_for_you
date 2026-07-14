# Deploying the job checklist as a website (GitHub Pages)

This turns the local script into a real website that updates itself automatically
every day, lives at **jobs.placeonus.com**, and requires signing in with your
email before anyone can see it. Here's how the pieces fit together:

1. A GitHub Actions workflow (`.github/workflows/daily-job-check.yml`) runs on
   GitHub's servers once a day.
2. It runs `job_checker.py` with `config.web.json`, which checks all sources and
   updates `data/jobs_found.csv` and `docs/index.html`.
3. It commits and pushes those changes back to your repo.
4. GitHub Pages serves whatever is in `docs/` at your custom domain.
5. Visiting the site shows a sign-in screen (Firebase email-link, no password).
   Once signed in, everyone who's signed in can see the same checklist and see
   each other's application status per job ("Also: alice@x.com (Applied)").
6. Each signed-in user can also upload their own resume (Firebase Storage) and,
   optionally, tailor it to a specific job with their own OpenAI key (Cloud
   Functions) — both of these are per-account and private to that user.

## One-time setup

About 30–40 minutes total, mostly Firebase + DNS. Add roughly 10 more minutes
if you also set up the optional AI resume tailoring feature (step 5 below).

### 1. Create the repository

1. Go to https://github.com/new
2. Name it something like `job-checklist` (**must be public** — free GitHub
   accounts can only use Pages on public repos; the login screen is what keeps
   it from being usable by randoms who stumble on the URL).
3. Create it empty (no README/gitignore from GitHub's side, we already have those).

### 2. Push this folder to the repo

```
git init
git add .
git commit -m "Initial job checklist site"
git branch -M main
git remote add origin https://github.com/<your-username>/job-checklist.git
git push -u origin main
```

### 3. Set up Firebase (email login + shared "who applied" tracking)

1. Go to https://console.firebase.google.com → **Add project** → name it
   anything (e.g. `job-checklist`) → you can skip Google Analytics → Create.
2. **Enable email-link sign-in:** in the left sidebar, **Build → Authentication**
   → **Get started** → under "Sign-in method", choose **Email/Password** →
   toggle it on → also toggle on **Email link (passwordless sign-in)** → Save.
3. **Add authorized domains:** still in Authentication, go to **Settings** tab
   → **Authorized domains** → **Add domain** → add `jobs.placeonus.com`.
   (Your `*.firebaseapp.com` domain and `localhost` are already there by default.)
4. **Create the database:** in the left sidebar, **Build → Firestore Database**
   → **Create database** → choose a region close to you → start in
   **production mode**.
5. **Set security rules:** in Firestore, go to the **Rules** tab and replace the
   contents with:
   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /job_status/{docId} {
         allow read: if request.auth != null;
         allow write: if request.auth != null
                       && request.auth.token.email == request.resource.data.email;
       }
       match /user_resumes/{uid} {
         allow read: if request.auth != null && request.auth.uid == uid;
         allow write: if request.auth != null && request.auth.uid == uid;
       }
     }
   }
   ```
   This lets any signed-in user see everyone's status (so "who applied" works),
   but only ever write their *own* status — nobody can edit someone else's entry.
   The `user_resumes` rule keeps each person's resume text and file link
   private to just them — nobody else can read or write it, including you.
   Click **Publish**.
6. **Get your web config:** click the gear icon → **Project settings** →
   scroll to "Your apps" → click the **</>** (web) icon → register an app
   (nickname anything, skip Firebase Hosting) → it shows a `firebaseConfig`
   object. Copy those values into `docs/firebase-config.js` in this repo,
   replacing the `REPLACE_ME` placeholders. Commit and push that change.

This file (`docs/firebase-config.js`) is intentionally separate from
`docs/index.html` — the daily workflow only ever rewrites `index.html`, so your
real Firebase config never gets overwritten by the automated run.

### 4. Set up Firebase Storage (per-user resumes)

This lets each signed-in user upload their own resume file — the site's "My
Resume" button always shows *their* file, never a shared default.

1. In the Firebase console, left sidebar → **Build → Storage** → **Get
   started** → choose the same region you picked for Firestore → start in
   **production mode**.
2. Go to the **Rules** tab and replace the contents with what's in
   `storage.rules` in this repo:
   ```
   rules_version = '2';
   service firebase.storage {
     match /b/{bucket}/o {
       match /resumes/{uid}/{fileName} {
         allow read: if request.auth != null && request.auth.uid == uid;
         allow write: if request.auth != null && request.auth.uid == uid
                       && request.resource.size < 10 * 1024 * 1024
                       && request.resource.contentType.matches('application/pdf|application/msword|application/vnd.openxmlformats-officedocument.wordprocessingml.document');
       }
     }
   }
   ```
   This keeps every resume file private to the person who uploaded it (max
   10MB, PDF/Word only). Click **Publish**.

That's it for Storage — no code deploy needed for this part. Once this is
published, the site's Settings panel lets any signed-in user upload a resume
and it'll be saved under their own account automatically.

### 5. Set up AI resume tailoring (optional — requires a paid Firebase plan)

This adds the "Tailor" button next to each job, which rewrites a user's
resume text to better match that specific role using OpenAI's API. You can
skip this section entirely — everything else on the site works fine without
it, the Tailor button will just show a message saying it isn't set up yet.

**Why this needs a Cloud Function:** OpenAI's API doesn't allow direct calls
from a browser (no CORS support), so a small server-side relay is required.
That relay is already written for you in `functions/index.js` — you just
need to deploy it.

**This step requires upgrading your Firebase project to the "Blaze"
(pay-as-you-go) plan.** Cloud Functions cannot run on the free "Spark" plan.
In practice this usually costs very little for personal use (Firebase's free
tier of 2 million function invocations/month still applies on Blaze — you
only pay if you exceed it), but you should know it's no longer strictly $0.
Any actual OpenAI usage is billed separately to your own OpenAI account,
using the API key each user enters themselves.

1. In the Firebase console, click **Upgrade** (bottom-left) → choose
   **Blaze** → attach a billing account.
2. Install the Firebase CLI on your computer (one-time):
   ```
   npm install -g firebase-tools
   ```
3. Log in and select your project:
   ```
   firebase login
   firebase use jobs-we-care-you
   ```
   (Run this from inside the folder you cloned this repo into, since
   `firebase.json` and `.firebaserc` already point at the right project.)
4. Deploy the function:
   ```
   firebase deploy --only functions
   ```
5. The CLI output ends with a line like:
   ```
   Function URL (tailorResume(us-central1)): https://us-central1-jobs-we-care-you.cloudfunctions.net/tailorResume
   ```
   Copy everything **before** `/tailorResume` and paste it into
   `docs/firebase-config.js` as the `cloudFunctionsBaseUrl` value, e.g.:
   ```js
   cloudFunctionsBaseUrl: "https://us-central1-jobs-we-care-you.cloudfunctions.net"
   ```
   Commit and push that change.
6. On the site, each user opens **Settings**, pastes their own OpenAI API
   key (from https://platform.openai.com/api-keys) and their resume text,
   then clicks **Tailor** on any job. The key is stored only in that
   person's own browser (`localStorage`) and sent directly to your Cloud
   Function per-request — it is never written to Firestore or logged.

### 6. Point jobs.placeonus.com at GitHub Pages

1. In your DNS provider for `placeonus.com`, add a **CNAME record**:
   - Host/name: `jobs`
   - Value/target: `<your-username>.github.io`
   - TTL: default is fine
2. In your repo: **Settings → Pages**.
   - Under "Build and deployment", set **Source** to `Deploy from a branch`.
   - Set **Branch** to `main`, folder to `/docs`, **Save**.
   - Under "Custom domain", enter `jobs.placeonus.com` → **Save**. (A
     `docs/CNAME` file with that domain is already in this repo, so GitHub
     should pick it up automatically, but entering it here too makes sure
     HTTPS gets provisioned.)
   - Once GitHub verifies the DNS (can take a few minutes to a few hours),
     check **Enforce HTTPS**.

Until DNS propagates you can still preview the site at
`https://<your-username>.github.io/job-checklist/`.

### 7. (Optional but recommended) Add Adzuna / USAJobs as secrets

These are the two highest-volume sources and only need a free key each:

1. Adzuna: register at https://developer.adzuna.com/ → copy your `app_id` and `app_key`.
2. USAJobs: register at https://developer.usajobs.gov/APIRequest/Index → copy your email and API key.
3. In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
4. Add these four secrets (names must match exactly):
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `USAJOBS_EMAIL`
   - `USAJOBS_API_KEY`

You don't need to touch `config.web.json` for this — the workflow passes secrets
in as environment variables and the script checks those automatically.

### 8. Trigger the first run

1. Go to the **Actions** tab in your repo.
2. Click **Daily Job Check** in the left sidebar.
3. Click **Run workflow** (this is the manual trigger — `workflow_dispatch`) to
   confirm everything works before waiting for the schedule.
4. Once it finishes (green checkmark), visit `https://jobs.placeonus.com`
   (or the github.io URL if DNS hasn't propagated yet) — you should see the
   sign-in screen. Enter your email, check your inbox for the link, click it,
   and you'll land back on the site signed in.

From here on, it runs automatically every day at 13:00 UTC (8am US Eastern /
5am US Pacific) with no action needed from you. Change the time by editing the
`cron:` line in `.github/workflows/daily-job-check.yml` — https://crontab.guru
is a handy reference for the syntax.

## How the login + tracking actually works

- Sign-in is passwordless: you type your email, Firebase emails you a one-time
  link, clicking it signs you in on that device/browser. No passwords to manage.
- Every job's status dropdown ("To Apply" / "Applied" / "Interview Scheduled" /
  "Interviewed" / "Offer" / "Rejected") is saved to a shared Firestore database,
  tagged with your email — so if you invite someone else to sign in (e.g. a
  friend also job-hunting, or someone helping you), you'll each see your own
  status per job, plus a small "Also: their-email (their-status)" note under
  each row showing what others have done.
- Add more people simply by telling them the URL — anyone can sign in with any
  email (there's no invite-only allowlist by default). If you want to restrict
  who can sign in at all, the simplest option is adding an `allowedEmails`
  check in the Firestore rules — let me know if you want that added.

## How the per-user resume + AI tailoring works

- Each signed-in user has their own **Settings** panel (gear icon in the top
  bar) where they can upload a resume file (PDF/Word), paste their resume as
  plain text, and enter their own OpenAI API key.
- The uploaded file goes to Firebase Storage under `resumes/{their-uid}/...`
  and is only ever readable by that same account — there is no shared/default
  resume shown to everyone; the topbar button says "Upload Resume" until a
  user adds their own, then switches to "My Resume" linking to their file.
- The resume text and file link are saved to Firestore under
  `user_resumes/{their-uid}`, again private to just that account.
- Clicking **Tailor** on any job sends that user's resume text, their own
  OpenAI key, and the job's title/company/location to your `tailorResume`
  Cloud Function, which relays the request to OpenAI and returns a rewritten
  resume. Nothing from this step is stored anywhere — it happens live, on
  demand, per click. If Cloud Functions haven't been deployed yet (step 5 is
  optional), the button explains that clearly instead of failing silently.

## Keeping the local Mac version too

Nothing about this changes the local setup — `config.json` (with the Desktop
path, no login, no Firebase) and `config.web.json` (with the hosted site
settings) are separate files, so you can still run `python3 job_checker.py`
locally for your own CSV on your Desktop, completely independent of the website.

## Files specific to the website

| File | Purpose |
|---|---|
| `docs/index.html` | Generated automatically each run — the login-gated checklist page |
| `docs/firebase-config.js` | Your Firebase project config — edit once by hand, never auto-generated |
| `docs/CNAME` | Tells GitHub Pages to serve at `jobs.placeonus.com` |
| `docs/resume.pdf` / `docs/resume.docx` | Unused legacy files from before per-user resumes — safe to leave or delete, no longer linked from the site |
| `storage.rules` | Firebase Storage security rules — each user can only read/write their own resume file |
| `firebase.json` / `.firebaserc` | Tell the Firebase CLI where your Cloud Functions code lives and which project to deploy to |
| `functions/index.js` | The `tailorResume` Cloud Function — relays AI tailoring requests to OpenAI (deploy with `firebase deploy --only functions`) |

## Troubleshooting

- **Workflow fails on the "Run job checker" step:** check the Actions log —
  most likely one of the company boards started requiring auth, or a typo in
  a secret name. Individual source failures are logged but don't stop the run.
- **Site shows old data:** GitHub Pages can take a minute to update after a
  push. Hard-refresh (Cmd+Shift+R) if it looks stale.
- **Nothing shows up at all:** double check Pages is set to serve from
  `main` / `/docs`, not `/ (root)`.
- **"Upload Resume" button doesn't do anything / upload fails:** confirm
  Firebase Storage is set up (step 4) and the rules were published — check
  the browser console for a permission-denied error, which usually means the
  Storage rules weren't published yet.
- **"Tailor" button says AI tailoring isn't set up:** you skipped or haven't
  finished step 5 (Cloud Functions) — either deploy it, or ignore the button,
  everything else on the site works without it.
- **"Tailor" button shows an OpenAI error:** almost always an invalid/expired
  API key or no billing set up on the user's own OpenAI account — this is
  between that user and OpenAI, not a bug in the site.
- **"Sign-in failed" or the link doesn't work:** confirm `jobs.placeonus.com`
  is in Firebase Auth's Authorized domains list, and that
  `docs/firebase-config.js` has your real project values, not the
  `REPLACE_ME` placeholders.
- **You see your own status but not others':** double-check the Firestore
  rules were published (step 3.5) — the `allow read: if request.auth != null`
  rule is what lets signed-in users see everyone's entries.
