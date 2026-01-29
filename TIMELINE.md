-> Add another ml job to automatically look into failed searches instead of doing that manual step (status='queued' in FailedSearchs) (Ranker and provider)
-> Host online
-> CRON job to automatically run the nlp code and download and ingest the usda data.
(Right now i manually run nlp_food_filter.py to filter junk food) (docker compose exec api python -m app.etl.process_failed_searches)
(Then I manually run pipeline.py from papers to find valid papers) (docker compose exec api python -m app.papers.pipeline )
(To implement - pipeline to go actually read through the paper to fill amino acid table from valid papers)
-> Pretty up the UI - bare minimum for now
-> Paper ranker (switch from deterministic ranks to TF-IDF vectorisation then semantic searches) (focus on model to skim the papers)
