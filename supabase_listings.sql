-- 관심 매물 서버 저장용 Supabase 스키마
-- Supabase Dashboard > SQL Editor에서 실행하세요.
-- 2026-11-22: 스키마·함수는 이미 적용됨. 맨 아래 PIN INSERT만 실행하면 됩니다.
-- pgcrypto가 extensions 스키마에 있어 crypt/gen_salt는 extensions. 접두사가 필요합니다.
-- IMPORTANT: 맨 아래 CHANGE_THIS_PIN_NOW 를 본인만 아는 PIN/암호로 바꾼 뒤 실행하세요.

create extension if not exists pgcrypto;

create table if not exists public.buyer_settings (
  id integer primary key default 1 check (id = 1),
  pin_hash text not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.buyer_listings (
  id text primary key,
  apt text not null,
  dong text not null default '',
  price integer not null check (price > 0), -- 만원
  area numeric(8,2),
  floor integer,
  url text not null default '',
  memo text not null default '',
  source text not null default 'manual',
  source_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists buyer_listings_source_uidx
  on public.buyer_listings(source, source_id)
  where source_id is not null and source_id <> '';

create table if not exists public.buyer_listing_prices (
  id bigint generated always as identity primary key,
  listing_id text not null references public.buyer_listings(id) on delete cascade,
  price integer not null check (price > 0),
  observed_at timestamptz not null default now()
);

create index if not exists buyer_listing_prices_listing_idx
  on public.buyer_listing_prices(listing_id, observed_at);

alter table public.buyer_settings enable row level security;
alter table public.buyer_listings enable row level security;
alter table public.buyer_listing_prices enable row level security;

-- 테이블 자체는 anon/authenticated에 공개하지 않는다. RPC만 사용.
revoke all on public.buyer_settings from anon, authenticated;
revoke all on public.buyer_listings from anon, authenticated;
revoke all on public.buyer_listing_prices from anon, authenticated;
revoke all on sequence public.buyer_listing_prices_id_seq from anon, authenticated;

create or replace function public.buyer_pin_ok(p_pin text)
returns boolean
language sql
security definer
set search_path = public, extensions
as $$
  select exists (
    select 1 from public.buyer_settings
    where id = 1 and pin_hash = extensions.crypt(coalesce(p_pin,''), pin_hash)
  );
$$;
revoke all on function public.buyer_pin_ok(text) from public, anon, authenticated;

create or replace function public.buyer_listings_list(p_pin text)
returns table (
  id text, apt text, dong text, price integer, area numeric, floor integer,
  url text, memo text, source text, source_id text,
  created_at timestamptz, updated_at timestamptz, history jsonb
)
language plpgsql
security definer
set search_path = public, extensions
as $$
begin
  if not public.buyer_pin_ok(p_pin) then
    raise exception 'bad_pin' using errcode = '28000';
  end if;
  return query
  select l.id, l.apt, l.dong, l.price, l.area, l.floor, l.url, l.memo,
         l.source, l.source_id, l.created_at, l.updated_at,
         coalesce(
           (select jsonb_agg(jsonb_build_object('at', p.observed_at, 'price', p.price) order by p.observed_at)
            from public.buyer_listing_prices p where p.listing_id = l.id),
           '[]'::jsonb
         ) as history
  from public.buyer_listings l
  order by l.updated_at desc;
end;
$$;

create or replace function public.buyer_listing_upsert(
  p_pin text, p_id text, p_apt text, p_dong text, p_price integer,
  p_area numeric default null, p_floor integer default null,
  p_url text default '', p_memo text default '',
  p_source text default 'manual', p_source_id text default null,
  p_observed_at timestamptz default now()
)
returns text
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_id text := nullif(trim(coalesce(p_id,'')), '');
  v_old_price integer;
begin
  if not public.buyer_pin_ok(p_pin) then
    raise exception 'bad_pin' using errcode = '28000';
  end if;
  if trim(coalesce(p_apt,'')) = '' or p_price is null or p_price <= 0 then
    raise exception 'invalid_listing';
  end if;

  -- 제휴 API 연동 시 source+source_id로 동일 매물을 자동 식별
  if nullif(trim(coalesce(p_source_id,'')), '') is not null then
    select id into v_id from public.buyer_listings
     where source = coalesce(nullif(trim(p_source),''),'manual') and source_id = trim(p_source_id)
     limit 1;
  end if;
  if v_id is null then
    v_id := 'm' || replace(gen_random_uuid()::text, '-', '');
  end if;

  select price into v_old_price from public.buyer_listings where id = v_id;

  insert into public.buyer_listings(id, apt, dong, price, area, floor, url, memo, source, source_id, created_at, updated_at)
  values (v_id, trim(p_apt), trim(coalesce(p_dong,'')), p_price, p_area, p_floor,
          trim(coalesce(p_url,'')), coalesce(p_memo,''), coalesce(nullif(trim(p_source),''),'manual'),
          nullif(trim(coalesce(p_source_id,'')),''), now(), p_observed_at)
  on conflict (id) do update set
    apt = excluded.apt, dong = excluded.dong, price = excluded.price,
    area = excluded.area, floor = excluded.floor, url = excluded.url, memo = excluded.memo,
    source = excluded.source, source_id = excluded.source_id, updated_at = excluded.updated_at;

  if v_old_price is null or v_old_price <> p_price then
    insert into public.buyer_listing_prices(listing_id, price, observed_at)
    values (v_id, p_price, p_observed_at);
  end if;
  return v_id;
end;
$$;

create or replace function public.buyer_listing_delete(p_pin text, p_id text)
returns boolean
language plpgsql
security definer
set search_path = public, extensions
as $$
declare v_n integer;
begin
  if not public.buyer_pin_ok(p_pin) then
    raise exception 'bad_pin' using errcode = '28000';
  end if;
  delete from public.buyer_listings where id = p_id;
  get diagnostics v_n = row_count;
  return v_n > 0;
end;
$$;

grant execute on function public.buyer_listings_list(text) to anon, authenticated;
grant execute on function public.buyer_listing_upsert(text,text,text,text,integer,numeric,integer,text,text,text,text,timestamptz) to anon, authenticated;
grant execute on function public.buyer_listing_delete(text,text) to anon, authenticated;

-- ↓↓↓ 실행 전에 반드시 바꾸세요. 나중에 변경할 때도 이 INSERT만 새 PIN으로 다시 실행하면 됩니다. ↓↓↓
insert into public.buyer_settings(id, pin_hash, updated_at)
values (1, extensions.crypt('CHANGE_THIS_PIN_NOW', extensions.gen_salt('bf')), now())
on conflict (id) do update set pin_hash = excluded.pin_hash, updated_at = now();
