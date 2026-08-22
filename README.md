# Seagrass Research Radar

A trial-ready, static **GitHub Pages** website that automatically searches for the world's newest seagrass academic papers every day.

The site is deliberately **multi-index**, rather than a scraper for individual publisher websites:

1. **Crossref** — the publisher-deposited metadata backbone. This is the most important source for ensuring major publishers and very large numbers of smaller DOI-registering journals are included.
2. **OpenAlex** — broad independent scholarly index (API key recommended/expected for sustained use).
3. **Europe PMC** — PubMed plus additional life-science, agricultural and related literature.
4. **Semantic Scholar** — independent scholarly graph and a useful redundancy layer.
5. **DOAJ** — incremental OAI-PMH harvesting as an additional safety net for smaller open-access journals.

The dashboard also has an explicit publisher audit for **Springer Nature, Elsevier, Wiley, Frontiers, MDPI, PLOS, IOP Publishing, Taylor & Francis, SAGE, OUP, CUP, Royal Society, Copernicus, CSIRO, Inter-Research, BioOne, PeerJ, AGU and others**.

> Note: the publisher is **MDPI** (Multidisciplinary Digital Publishing Institute), not MDPS.

## What the Radar does

- searches every day;
- searches `seagrass`, `eelgrass`, marine angiosperm / marine phanerogam terminology and all major seagrass genera;
- includes legacy taxonomic combinations in scoring;
- merges duplicate records from different indexes by DOI and normalised title;
- records **first seen** separately from formal publication date;
- tags research themes and seagrass genera/species names;
- tags open-access status when supplied by an index;
- infers study countries from explicit title/abstract place names for the map;
- generates a seven-day digest automatically;
- optionally produces short AI summaries for newly discovered papers;
- shows both **publisher coverage** and **source health** on the public site.

## Fastest GitHub trial

### 1. Create the repository

Create a new public GitHub repository, for example:

`seagrass-research-radar`

Upload **all files and folders in this package** to the root of the repository.

### 2. Add the OpenAlex key

Create an OpenAlex API key and add it in:

**GitHub repository → Settings → Secrets and variables → Actions → New repository secret**

Name it exactly:

`OPENALEX_API_KEY`

### 3. Add a Crossref contact email

Add another repository secret:

`CROSSREF_MAILTO`

Set the value to an email address you are comfortable using for polite API identification. This is not displayed on the website.

### 4. Optional Semantic Scholar key

The Semantic Scholar adapter can work without a key subject to public limits, but a key gives more predictable access. If you have one, add:

`SEMANTIC_SCHOLAR_API_KEY`

If no key is supplied, the system still has Crossref + OpenAlex + Europe PMC + DOAJ.

### 5. Optional AI summaries

If you want the `Radar summary` / `Why it matters` boxes, add:

`OPENAI_API_KEY`

The action currently uses `gpt-5.6-luna` and limits summarisation to 12 newly discovered papers per run. Without this secret the site works normally, just without AI-generated summaries. You can set `AI_SUMMARY_LIMIT: "0"` in the workflow if you never want this feature.

### 6. Enable GitHub Pages

Go to:

**Settings → Pages → Build and deployment**

Choose:

- **Source:** Deploy from a branch
- **Branch:** `main`
- **Folder:** `/docs`

Save.

Your site will then be available at a URL similar to:

`https://YOUR-USERNAME.github.io/seagrass-research-radar/`

### 7. Run the first scan

Go to:

**Actions → Daily Seagrass Research Radar → Run workflow**

The workflow runs automatically every day at **05:17 UTC** thereafter.

## Recommended first-run settings

The workflow uses a 21-day overlapping retrieval window and retains three years of records. This overlap is intentional: publication databases do not all ingest papers on the same day.

For an initial historical fill, temporarily change:

`RETRIEVAL_LOOKBACK_DAYS: "21"`

to:

`RETRIEVAL_LOOKBACK_DAYS: "120"`

Run the workflow once, then change it back to 21. OpenAlex, Europe PMC and Semantic Scholar already search a longer publication window to catch recent material.

## Test locally

The Python harvester has no third-party package dependencies.

```bash
python -m unittest discover -s tests
python harvest.py --demo
python -m http.server 8000 --directory docs
```

Then visit `http://localhost:8000`.

For a real local scan, set environment variables first and run:

```bash
python harvest.py --no-ai
```

## Why not scrape Springer, Wiley, Elsevier, Frontiers, MDPI, PLOS and IOP directly?

Publisher-specific scraping is brittle: HTML changes, bot protection, different journal platforms, and licensing restrictions make it easy to miss papers. The Radar instead searches infrastructure that publishers themselves deposit into and then cross-checks that with independent indexes.

Crossref's daily `index-date` search is particularly valuable: it tracks **when metadata was indexed/updated**, not just the nominal publication date. That means an article published several days earlier but deposited late can still be surfaced as newly discovered.

## Interpreting the publisher coverage page

- **Seen** means at least one retained seagrass record has been attributed to that publisher group.
- **No recent hit** does **not** mean the publisher was excluded. It means the database does not currently contain a matching paper from that group in the retained data.
- **Harvester health** reports whether each external source ran successfully in the latest scan.

This is deliberately transparent because no public academic index can guarantee instant, literal 100% coverage of every journal worldwide.

## Files

```text
seagrass-research-radar/
├── harvest.py
├── README.md
├── requirements.txt
├── tests/
│   └── test_core.py
├── .github/
│   └── workflows/
│       └── daily-update.yml
└── docs/
    ├── index.html
    ├── styles.css
    ├── app.js
    └── data/
        ├── papers.json
        ├── status.json
        └── digest.json
```

## Search quality notes

`Ruppia` is deliberately treated more cautiously because the genus can occur in brackish/freshwater literature outside normal seagrass usage. `Posidonia` also has non-biological meanings, so the scorer looks for ecological/taxonomic context. These rules can be tuned in `harvest.py`.

## Suggested next upgrades after the trial

- add an RSS/Atom feed of newly discovered papers;
- add email or Teams/Slack weekly digest delivery;
- add manual `relevant / not relevant` feedback that writes to a small moderation file;
- add a journal-level watch list for especially important seagrass journals;
- add richer location extraction and study-system classification;
- move the historical database to SQLite/Supabase if the retained corpus becomes too large for one JSON file.
