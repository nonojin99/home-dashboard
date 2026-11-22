-- 건축물대장 캐시. supabase_listings.sql 실행 후 추가로 1회 실행하세요.
-- buyer_pin_ok()를 재사용하므로 PIN을 새로 만들 필요 없습니다.

create table if not exists public.building_info (
  complex_key text primary key,
  sgg_cd text not null,
  bjdong_cd text,
  dong text not null,
  jibun text not null,
  apt text not null,
  source_kind text not null default 'recap',
  households integer,
  parking integer,
  parking_per_household numeric(6,2),
  elevators integer,
  max_floor integer,
  floor_area_ratio numeric(8,2),
  building_coverage_ratio numeric(8,2),
  use_approval_date text,
  seismic boolean,
  main_purpose text,
  road_address text,
  raw_count integer,
  quality_score integer check (quality_score between 0 and 100),
  fetched_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists building_info_sgg_idx on public.building_info(sgg_cd, dong, jibun);
alter table public.building_info enable row level security;
revoke all on public.building_info from anon, authenticated;

-- GitHub Actions 적재. 관심매물 PIN을 아는 요청만 쓰기 가능.
create or replace function public.building_info_upsert(p_pin text, p_row jsonb)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare v_key text := p_row->>'complex_key';
begin
  if not public.buyer_pin_ok(p_pin) then
    raise exception 'bad_pin' using errcode = '28000';
  end if;
  if coalesce(v_key,'') = '' then raise exception 'invalid_complex_key'; end if;

  insert into public.building_info(
    complex_key, sgg_cd, bjdong_cd, dong, jibun, apt, source_kind,
    households, parking, parking_per_household, elevators, max_floor,
    floor_area_ratio, building_coverage_ratio, use_approval_date, seismic,
    main_purpose, road_address, raw_count, quality_score, fetched_at, updated_at
  ) values (
    v_key, p_row->>'sgg_cd', p_row->>'bjdong_cd', p_row->>'dong', p_row->>'jibun', p_row->>'apt',
    coalesce(p_row->>'source_kind','recap'),
    nullif(p_row->>'households','')::integer, nullif(p_row->>'parking','')::integer,
    nullif(p_row->>'parking_per_household','')::numeric, nullif(p_row->>'elevators','')::integer,
    nullif(p_row->>'max_floor','')::integer, nullif(p_row->>'floor_area_ratio','')::numeric,
    nullif(p_row->>'building_coverage_ratio','')::numeric, nullif(p_row->>'use_approval_date',''),
    case when p_row ? 'seismic' and p_row->'seismic' <> 'null'::jsonb then (p_row->>'seismic')::boolean else null end,
    p_row->>'main_purpose', p_row->>'road_address', nullif(p_row->>'raw_count','')::integer,
    nullif(p_row->>'quality_score','')::integer,
    coalesce(nullif(p_row->>'fetched_at','')::timestamptz, now()), now()
  )
  on conflict (complex_key) do update set
    bjdong_cd=excluded.bjdong_cd, source_kind=excluded.source_kind,
    households=excluded.households, parking=excluded.parking,
    parking_per_household=excluded.parking_per_household, elevators=excluded.elevators,
    max_floor=excluded.max_floor, floor_area_ratio=excluded.floor_area_ratio,
    building_coverage_ratio=excluded.building_coverage_ratio,
    use_approval_date=excluded.use_approval_date, seismic=excluded.seismic,
    main_purpose=excluded.main_purpose, road_address=excluded.road_address,
    raw_count=excluded.raw_count, quality_score=excluded.quality_score,
    fetched_at=excluded.fetched_at, updated_at=now();
  return v_key;
end;
$$;

-- 최근 N일 안에 갱신되어 다시 API를 호출할 필요가 없는 key 목록.
create or replace function public.building_info_stale_keys(p_days integer default 45)
returns table(complex_key text)
language sql
security definer
set search_path = public
as $$
  select b.complex_key from public.building_info b
  where b.fetched_at >= now() - make_interval(days => greatest(1, least(coalesce(p_days,45),365)));
$$;

-- 현재 화면에 필요한 단지만 한 번에 조회. 공개 정보라 읽기는 PIN 없이 허용.
create or replace function public.building_info_lookup(p_keys jsonb)
returns setof public.building_info
language sql
security definer
set search_path = public
stable
as $$
  select b.* from public.building_info b
  join jsonb_array_elements_text(coalesce(p_keys,'[]'::jsonb)) k on k.value = b.complex_key;
$$;

grant execute on function public.building_info_upsert(text,jsonb) to anon, authenticated;
grant execute on function public.building_info_stale_keys(integer) to anon, authenticated;
grant execute on function public.building_info_lookup(jsonb) to anon, authenticated;
