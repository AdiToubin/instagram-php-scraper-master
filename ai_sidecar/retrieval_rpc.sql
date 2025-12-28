-- 1. Enable Extensions
create extension if not exists vector;
create extension if not exists pgcrypto;

-- 2. Create Memory Table
create table if not exists stories_memory (
  id uuid default gen_random_uuid() primary key,
  media_id text not null unique,
  user_id text,
  
  -- Context
  context_text text not null,
  embedding vector(1536),
  
  -- Decisions
  verdict text not null check (verdict in ('coupon', 'collab_ad', 'recommendation', 'organic')),
  extracted_data jsonb default '{}'::jsonb,
  
  -- Hygiene
  confidence float,
  is_ground_truth boolean default false,
  verification_source text,
  
  created_at timestamptz default now()
);

-- 3. Indexes
create index if not exists idx_stories_memory_embedding on stories_memory using hnsw (embedding vector_cosine_ops);
create index if not exists idx_memory_user on stories_memory(user_id);
create index if not exists idx_memory_verdict on stories_memory(verdict);
create index if not exists idx_memory_ground_truth on stories_memory(is_ground_truth);

-- 4. Hybrid Search RPC Function
create or replace function hybrid_search(
  query_vec vector(1536),
  match_threshold float,
  match_count int,
  filter_user_id text default null,
  filter_verdict text default null
)
returns table (
  id uuid,
  context_text text,
  verdict text,
  extracted_data jsonb,
  similarity float,
  is_ground_truth boolean
)
language plpgsql
as $$
begin
  return query
  select
    sm.id,
    sm.context_text,
    sm.verdict,
    sm.extracted_data,
    1 - (sm.embedding <=> query_vec) as similarity,
    sm.is_ground_truth
  from stories_memory sm
  where 1 - (sm.embedding <=> query_vec) > match_threshold
  and sm.embedding is not null
  and (filter_verdict is null or sm.verdict = filter_verdict)
  -- Boosting Logic: prioritize user matches heavily
  order by
    (case when filter_user_id is not null and sm.user_id = filter_user_id then 1 else 0 end) desc,
    sm.is_ground_truth desc, -- Prefer confirmed truths
    similarity desc
  limit match_count;
end;
$$;
