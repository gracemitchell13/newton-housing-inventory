# Newton Affordable Housing — Web App

## Local development (test before deploying)

### 1. Install dependencies
```bash
cd newton-shi
pip3 install -r requirements.txt
```

### 2. Set up your .env file
```bash
cp .env.example .env
```
The default value (`postgresql://localhost/newton_shi`) works if your database
is running locally via Postgres.app.

### 3. Run the server
```bash
uvicorn main:app --reload
```

Open http://localhost:8000 — you should see the full dashboard with live data.

---

## Deploy to Railway (public URL)

### Step 1 — Push your code to GitHub
```bash
cd newton-shi
git init
git add .
git commit -m "Initial commit"
gh repo create newton-shi --public --push --source=.
```
(If you don't have the `gh` CLI: create a repo on github.com manually, then
follow the "push existing repo" instructions.)

### Step 2 — Create a Railway project
1. Go to railway.app and sign in with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Select your `newton-shi` repo
4. Railway will detect the Python app automatically

### Step 3 — Add a Postgres database on Railway
1. In your Railway project, click **+ New** → **Database** → **PostgreSQL**
2. Railway creates a hosted Postgres instance and gives you a `DATABASE_URL`

### Step 4 — Set the environment variable
1. Click your web service (not the database) in Railway
2. Go to **Variables** tab
3. Add: `DATABASE_URL` = (paste the value from the Postgres plugin's
   **Connect** tab — use the "Postgres Connection URL")

### Step 5 — Load your data into the Railway database
Run the ETL script against the Railway database URL:
```bash
python3 etl_load.py \
  --excel path/to/81733906.xlsx \
  --db "postgresql://..." # paste Railway's DATABASE_URL here
```

### Step 6 — Deploy
Railway deploys automatically on every push to GitHub. Your public URL will
appear in the Railway dashboard under **Settings → Domains**.

---

## Updating the data

When the Excel file is updated:
```bash
# Clear existing data
psql YOUR_RAILWAY_DB_URL -c "TRUNCATE properties, organizations CASCADE;"

# Re-run ETL
python3 etl_load.py --excel new_file.xlsx --db YOUR_RAILWAY_DB_URL
```

## File structure
```
newton-shi/
├── main.py          # FastAPI backend + API routes
├── index.html       # Frontend (served by FastAPI at /)
├── requirements.txt
├── railway.toml     # Railway deployment config
├── .env             # Local env vars (never commit this)
└── .env.example     # Safe to commit
```
