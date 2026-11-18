# Cleantech Challenge 2027 – team builder

Two ways to use it, sharing the same matching engine (`team_builder.py`) and settings (`config.py`):

- **Dashboard** (`streamlit run app.py`): review applicants, view LinkedIn and CVs, explore keyword and skill insights, build and adjust teams, with sign-in and roles. Data is kept in `data/` locally, or in a free Neon Postgres database when `DATABASE_URL` is set. See DEPLOY.md to put it online for free.
- **Command line** (`python team_builder.py fetch | cvs | check | build`): the same steps without a browser.

## How matching works

Interests (areas picked, bio, idea description and CV keywords) and time zone decide who is grouped together; every team gets someone with an idea and someone without, and teams closer to half-and-half score higher; background, skills and finals attendance break ties; teams are 3–6 people; the weakest interest fits go to an Unassigned list with reasons.

- **Repeat submissions:** one person per email; the latest submission is used.
- **Review:** blank means included unless marked no (`INCLUDE_BY_DEFAULT` in config.py).
- **Time zones:** each country maps to its UTC offset during the competition (`countries.py`); within 1 hour is a full match, falling to none at 6 hours apart.
- **Keywords:** candidate phrases are runs of content words between stop words and punctuation (RAKE), scored with TF-IDF across all applicants, kept if shared by at least 2 people.
- **LinkedIn** profiles aren't read (LinkedIn forbids scraping); links are shown for reviewers.

## Files

| File | What it is |
|---|---|
| `app.py`, `ctc_app/` | the dashboard |
| `team_builder.py` | matching engine and command-line tool |
| `config.py` | column mapping, keywords, weights, team sizes |
| `countries.py` | country → time zone and map codes |
| `.streamlit/config.toml` | dashboard theme |
| `.streamlit/secrets.toml.example` | Tally key, sign-in and access list template |
| `ctc_app/store.py` | storage: local files or Postgres |
| `DEPLOY.md`, `render.yaml` | free hosting (Streamlit Community Cloud + Neon; Render as an alternative) |
