create table if not exists ride_votes (
  id integer primary key autoincrement,
  ride_slug text not null,
  rating integer not null check (rating between 1 and 5),
  voter_hash text not null,
  ip_hash text not null,
  user_agent_hash text not null,
  created_at text not null default current_timestamp,
  unique (ride_slug, voter_hash)
);

create index if not exists ride_votes_ride_slug_idx
  on ride_votes (ride_slug);

create index if not exists ride_votes_ip_hash_created_idx
  on ride_votes (ip_hash, created_at);

create index if not exists ride_votes_voter_hash_created_idx
  on ride_votes (voter_hash, created_at);

create table if not exists ride_rating_summary (
  ride_slug text primary key,
  vote_count integer not null default 0,
  rating_sum integer not null default 0,
  average_rating real not null default 0,
  updated_at text not null default current_timestamp
);
