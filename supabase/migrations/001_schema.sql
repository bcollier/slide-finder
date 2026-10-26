-- Slide Finder schema. Tables are prefixed sf_ because they share a project with Faculty Twin.
-- Safe to re-run: everything is "if not exists" or "create or replace".

create extension if not exists vector;

-- One row per unique slide.
create table if not exists sf_slides (
  slide_id      text primary key,          -- e.g. 70445-s05-070
  course        text not null,             -- "70-445"
  course_name   text not null,
  term          text not null,             -- "2026 Fall"
  session       int  not null,
  session_title text not null default '',
  session_date  date,
  slide_number  int  not null,
  title         text not null default '',
  text          text not null default '',
  notes         text not null default '',
  description   text not null default '',  -- written by a vision model, optional
  image_path    text,                      -- object path in the slide-finder bucket
  thumb_path    text,
  used_in       jsonb not null default '[]',
  created_at    timestamptz not null default now()
);
create index if not exists sf_slides_deck on sf_slides (course, session, slide_number);

-- What gets embedded: several items per slide (card, said, note).
create table if not exists sf_items (
  id            text primary key,          -- <slide_id>-<kind>-<n>
  slide_id      text not null references sf_slides (slide_id) on delete cascade,
  kind          text not null check (kind in ('card', 'said', 'note')),
  term          text not null,
  occurrence_id text,
  text          text not null,
  embedding     halfvec(1024),             -- voyage-3.5
  fts           tsvector generated always as (to_tsvector('english', text)) stored,
  created_at    timestamptz not null default now()
);
create index if not exists sf_items_embedding on sf_items using hnsw (embedding halfvec_cosine_ops);
create index if not exists sf_items_fts       on sf_items using gin (fts);
create index if not exists sf_items_slide     on sf_items (slide_id);

-- What I said over a slide, per term and session.
create table if not exists sf_occurrences (
  id          text primary key,            -- <slide_id>@<term>
  slide_id    text not null references sf_slides (slide_id) on delete cascade,
  term        text not null,
  session     int  not null,
  windows     jsonb not null default '[]', -- [[start_s, end_s], ...]
  method      text,                        -- frame | text
  confidence  text,
  transcript  text not null default ''
);
create index if not exists sf_occurrences_slide on sf_occurrences (slide_id);

-- Slides picked for a new deck, in order.
create table if not exists sf_picks (
  position  serial primary key,
  slide_id  text not null references sf_slides (slide_id) on delete cascade,
  added_at  timestamptz not null default now()
);

-- Hybrid search: top matches by meaning and by keywords, fused by reciprocal rank.
-- distance is cosine distance (0 = identical); vec_rank/fts_rank are null when that side missed.
create or replace function sf_search(
  query_embedding halfvec(1024),
  query_text      text,
  match_count     int  default 50,
  course_filter   text default null
)
returns table (
  id text, slide_id text, kind text, term text, text text,
  vec_rank int, fts_rank int, distance double precision, score double precision
)
language sql stable as $$
  with v as (
    select i.id,
           row_number() over (order by i.embedding <=> query_embedding) as r,
           (i.embedding <=> query_embedding)::double precision as d
    from sf_items i join sf_slides s using (slide_id)
    where i.embedding is not null
      and (course_filter is null or s.course = course_filter)
    order by i.embedding <=> query_embedding
    limit match_count
  ),
  f as (
    select i.id, row_number() over (order by ts_rank_cd(i.fts, q) desc) as r
    from sf_items i join sf_slides s using (slide_id),
         websearch_to_tsquery('english', query_text) q
    where i.fts @@ q
      and (course_filter is null or s.course = course_filter)
    order by ts_rank_cd(i.fts, q) desc
    limit match_count
  )
  select i.id, i.slide_id, i.kind, i.term, i.text,
         v.r::int, f.r::int, v.d,
         coalesce(1.0 / (60 + v.r), 0) + coalesce(1.0 / (60 + f.r), 0) as score
  from sf_items i
  left join v on v.id = i.id
  left join f on f.id = i.id
  where v.id is not null or f.id is not null
  order by score desc;
$$;

-- Lets the build machine apply later migrations through the REST API.
-- Only the service role may call it, and the service role could already do anything.
create or replace function sf_exec(sql text) returns void
language plpgsql security definer set search_path = public as $$
begin
  execute sql;
end
$$;
revoke all on function sf_exec(text) from public, anon, authenticated;
grant execute on function sf_exec(text) to service_role;

-- No public access to anything.
alter table sf_slides      enable row level security;
alter table sf_items       enable row level security;
alter table sf_occurrences enable row level security;
alter table sf_picks       enable row level security;
