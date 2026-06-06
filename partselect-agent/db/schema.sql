-- db/schema.sql
create extension if not exists vector;

create table if not exists appliance_models (
  id             bigserial primary key,
  model_number   text unique not null,
  brand          text not null,
  appliance_type text not null check (appliance_type in ('refrigerator','dishwasher'))
);

create table if not exists parts (
  id             bigserial primary key,
  ps_number      text unique not null,
  mpn            text,
  name           text not null,
  description    text,
  price_cents    int not null,
  in_stock       boolean not null default true,
  image_url      text,
  rating         numeric(2,1),
  review_count   int default 0,
  appliance_type text not null check (appliance_type in ('refrigerator','dishwasher')),
  video_url      text
);

create table if not exists part_cross_refs (
  part_id    bigint references parts(id) on delete cascade,
  alt_number text not null,
  primary key (part_id, alt_number)
);
create index if not exists idx_part_cross_refs_alt on part_cross_refs (alt_number);

create table if not exists part_compatibility (
  part_id  bigint references parts(id) on delete cascade,
  model_id bigint references appliance_models(id) on delete cascade,
  primary key (part_id, model_id)
);
create index if not exists idx_part_compat_model on part_compatibility (model_id);

create table if not exists symptoms (
  id             bigserial primary key,
  description    text unique not null,
  appliance_type text not null,
  embedding      vector(768)
);
create index if not exists idx_symptoms_embedding on symptoms using hnsw (embedding vector_cosine_ops);

create table if not exists part_symptoms (
  part_id    bigint references parts(id) on delete cascade,
  symptom_id bigint references symptoms(id) on delete cascade,
  primary key (part_id, symptom_id)
);

create table if not exists installation_steps (
  id          bigserial primary key,
  part_id     bigint references parts(id) on delete cascade,
  step_no     int not null,
  text        text not null,
  difficulty  text,
  est_minutes int,
  video_url   text
);

create table if not exists repair_guides (
  id              bigserial primary key,
  appliance_type  text not null,
  brand           text,
  title           text,
  body            text not null,
  source_url      text,
  likely_part_ids bigint[] default '{}',
  embedding       vector(768)
);
create index if not exists idx_repair_guides_embedding on repair_guides using hnsw (embedding vector_cosine_ops);

create table if not exists related_parts (
  part_id         bigint references parts(id) on delete cascade,
  related_part_id bigint references parts(id) on delete cascade,
  primary key (part_id, related_part_id)
);

create table if not exists carts (
  id uuid primary key default gen_random_uuid(),
  session_id text not null unique
);

create table if not exists cart_items (
  cart_id uuid references carts(id) on delete cascade,
  part_id bigint references parts(id) on delete cascade,
  qty     int not null default 1,
  primary key (cart_id, part_id)
);

create table if not exists orders (
  id           text primary key,
  session_id   text,
  status       text not null,
  tracking_url text
);

create table if not exists chat_sessions (
  session_id text primary key,
  messages   jsonb not null default '[]',
  updated_at timestamptz default now()
);
