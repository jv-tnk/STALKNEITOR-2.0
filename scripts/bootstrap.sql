create extension if not exists pgcrypto;

create type event_kind as enum ('problem_done','module_done','streak_extended','team_joined','note_shared');
create type progress_status as enum ('not_started','in_progress','skipped','done');

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  handle text unique,
  name text,
  created_at timestamptz default now()
);

create table if not exists teams (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  owner_id uuid references users(id) on delete set null,
  is_public boolean default false not null,
  created_at timestamptz default now()
);

create table if not exists team_members (
  team_id uuid references teams(id) on delete cascade,
  user_id uuid references users(id) on delete cascade,
  role text default 'member' not null,
  joined_at timestamptz default now() not null,
  primary key (team_id, user_id)
);

create table if not exists team_invites (
  id uuid primary key default gen_random_uuid(),
  team_id uuid references teams(id) on delete cascade,
  token text unique not null,
  role text default 'member' not null,
  expires_at timestamptz,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz default now() not null
);

create table if not exists modules (
  id uuid primary key default gen_random_uuid(),
  guide_module_id text not null unique,
  title text not null,
  division text not null,
  order_index int not null,
  url text not null,
  guide_version text,
  created_at timestamptz default now() not null,
  updated_at timestamptz default now() not null
);

create table if not exists problems (
  id uuid primary key default gen_random_uuid(),
  unique_id text not null unique,
  name text not null,
  url text not null,
  source text,
  difficulty text,
  tags text[],
  guide_module_id text references modules(guide_module_id),
  created_at timestamptz default now() not null,
  updated_at timestamptz default now() not null
);

create table if not exists module_progress (
  user_id uuid references users(id) on delete cascade,
  guide_module_id text references modules(guide_module_id) on delete cascade,
  status progress_status not null,
  percent int default 0 not null,
  updated_at timestamptz default now() not null,
  primary key (user_id, guide_module_id)
);

create table if not exists problem_progress (
  user_id uuid references users(id) on delete cascade,
  problem_id uuid references problems(id) on delete cascade,
  status progress_status not null,
  attempts int default 0 not null,
  last_result text,
  updated_at timestamptz default now() not null,
  primary key (user_id, problem_id)
);

create table if not exists notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id) on delete cascade,
  guide_module_id text references modules(guide_module_id) on delete cascade,
  problem_id uuid references problems(id) on delete set null,
  content text not null,
  visibility text default 'private' not null,
  created_at timestamptz default now() not null,
  updated_at timestamptz default now() not null
);

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  team_id uuid references teams(id) on delete cascade,
  user_id uuid references users(id) on delete cascade,
  kind event_kind not null,
  payload_json jsonb not null,
  created_at timestamptz default now() not null
);

create table if not exists etl_runs (
  id uuid primary key default gen_random_uuid(),
  commit_sha text not null,
  started_at timestamptz default now() not null,
  finished_at timestamptz,
  modules_upserted int default 0 not null,
  problems_upserted int default 0 not null,
  errors jsonb
);

create table if not exists problem_solutions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id) on delete cascade,
  problem_id uuid references problems(id) on delete cascade,
  guide_module_id text references modules(guide_module_id),
  content text not null,
  language text default 'plaintext',
  is_public boolean default true not null,
  created_at timestamptz default now() not null,
  updated_at timestamptz default now() not null
);

create index if not exists modules_division_order_idx on modules(division, order_index);
create index if not exists problems_module_idx on problems(guide_module_id);
create index if not exists team_members_user_idx on team_members(user_id);
create index if not exists teams_owner_idx on teams(owner_id);
create index if not exists events_team_created_at_idx on events(team_id, created_at);
