-- The HNSW index returns at most hnsw.ef_search (40) candidates per query, whatever the limit,
-- so "show 100 slides" came back with about 24. Supabase does not let the function change that
-- setting. At this size (under 10k items) an exact scan is a few milliseconds, so the vector
-- side orders by an expression the index cannot serve, which makes it exact and uncapped.
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
           row_number() over (order by (i.embedding <=> query_embedding) + 0.0) as r,
           (i.embedding <=> query_embedding)::double precision as d
    from sf_items i join sf_slides s using (slide_id)
    where i.embedding is not null
      and (course_filter is null or s.course = course_filter)
    order by (i.embedding <=> query_embedding) + 0.0
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
$$;
