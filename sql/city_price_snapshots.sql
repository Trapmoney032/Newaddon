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
