# Newaddon

Daily Python job that reads Bulgarian real-estate and banking RSS feeds and upserts them into the shared Supabase table `financial_news`. The URL is the unique key, so a story already stored is updated instead of inserted again.

This repository is the ingestion service for BezAgencia. It is developed here before it is wired into the platform.

## What it collects

| Source | What is kept | Category |
| --- | --- | --- |
| Investor.bg, property sections | Housing market headlines | `real_estate`, or a more specific category when the title matches |
| Investor.bg finance and economy, Economic.bg, Capital.bg | Only stories about homes, mortgages, or ownership costs | `real_estate`, `mortgages`, or `home_costs` |
| Bulgarian National Bank statistics | Interest-rate releases, including the monthly interest-rate bulletin | `mortgages` |

Stock-market and general political headlines are left out. The job also stores three short home tips: the municipal purchase tax, what sits on top of a mortgage payment, and how an annuity payment shifts from interest to principal.

| Category | For the reader |
| --- | --- |
| `real_estate` | What is happening to homes and neighbourhoods |
| `mortgages` | Housing loans and interest rates in Bulgaria |
| `home_costs` | Taxes, fees, insurance, bills, and building maintenance |

Each row is `title`, `source_name`, `url`, `category`, and `published_at`. A broad feed can add zero rows on a quiet day. That is not a failure. If one feed errors, the others still run and the process exits with status 1.

## Supabase table

Run this once in the Supabase SQL editor. The unique constraint on `url` is what the upsert uses.

```sql
create table if not exists public.financial_news (
  id bigint generated always as identity primary key,
  title text not null,
  source_name text not null,
  url text not null,
  category text not null,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  constraint financial_news_url_key unique (url)
);

alter table public.financial_news enable row level security;
```

The job authenticates with the **service role** key. That key bypasses row-level security, so keep it out of client apps and out of git. Add separate read policies later for the roles that should see this table in BezAgencia.

## Local setup

Python 3.12 or newer.

```powershell
cd C:\Projects\Newaddon
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set:

- `SUPABASE_URL` — Project Settings → API → Project URL
- `SUPABASE_SERVICE_KEY` — Project Settings → API → `service_role` secret

`.env` is gitignored. `config.py` loads it for local runs and leaves variables already set in the environment unchanged, which is how GitHub Actions supplies them.

```powershell
python scraper.py
python prices.py
```

`prices.py` loads the National Statistical Institute house price index for Sofia, Plovdiv, Varna, Burgas, Ruse, and Stara Zagora. The figure is an index with 2025 = 100, not a price in euro. Since 2022 the index covers apartments and houses together, so every row uses `property_type = dwellings` and `market = sale`. `dwelling_status` is `total`, `new`, or `existing`. A cell published as `/` is withheld by NSI and is not stored.

Run this once in the Supabase SQL editor before the first price sync. The same statement is in `sql/city_price_snapshots.sql`.

```sql
create table if not exists public.city_price_snapshots (
  id bigint generated always as identity primary key,
  city text not null,
  market text not null,
  property_type text not null,
  dwelling_status text not null,
  period_year int not null,
  period_quarter int not null,
  index_value numeric not null,
  base_year int not null,
  source_name text not null,
  source_url text not null,
  created_at timestamptz not null default now(),
  constraint city_price_snapshots_period_key unique (
    city,
    market,
    property_type,
    dwelling_status,
    period_year,
    period_quarter,
    source_name
  )
);

alter table public.city_price_snapshots enable row level security;
```

## GitHub Actions

The workflow `.github/workflows/daily_sync.yml` runs every day at 06:00 UTC and can also be started manually with **Run workflow**.

In the GitHub repository, open **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
| --- | --- |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase `service_role` key |

The workflow passes those secrets into the process environment. They are not written into the workflow file or the logs by this script.
