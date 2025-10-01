# N8N Sales Data Report Automation
This automation workflow is run from self-hosted instances of N8N and PostgreSQL. The PostgreSQL backend is populated with synthetic sales data for analytics and testing workflow success.

This repository contains:
- Docker Compose configuration and a custom `n8n` image build.
- A small data generation tool (`db-creator.py`) that creates a simple data model (districts, stores, reporting_time, items, sales) and loads it into Postgres.
- Utilities and notes for using community n8n nodes and lightweight visualization via QuickChart (or the n8n HTTP Request node).

## Table of contents
- [Architecture & files](#architecture--files)
- [Prerequisites](#prerequisites)
- [Quick start (bring the stack up)](#quick-start-bring-the-stack-up)
- [Database population (db-creator.py)](#database-population-db-creatorpy)
- [Inspecting & verifying data (psql examples)](#inspecting--verifying-data-psql-examples)
[n8n community nodes & visualization (QuickChart)](#n8n-community-nodes--visualization-quickchart)
- [Troubleshooting](#troubleshooting)
- [Development notes](#development-notes)
- [License](#license)

## Architecture & files
- `docker-compose.yml` — Compose file defining the `db` (Postgres) and `n8n` services.
- `Dockerfile.n8n` — Custom build steps for the n8n image (used to ensure community nodes can be available inside the image for some workflows).
- `db-creator.py` — Python script that creates schema and populates tables with synthetic sales data. It supports a `--force-recreate` mode to drop and recreate tables.
- `volumes/` — runtime volumes used by docker-compose (database files, n8n data, installed nodes, etc.). These are not uploaded.


## Prerequisites
- Docker & Docker Compose (v2+ recommended)
- Python 3.10+ (to run the population scripts locally if desired)
- `psql` client (optional, for manual verification)

The project uses an environment file (`.env`) for the n8n container environment. Make sure `.env` exists at the project root when running Compose.

## Quick start (bring the stack up)

1. Build and start the services:

```zsh
docker compose up -d --build
```

2. Check services:

```zsh
docker compose ps
docker compose logs -f n8n
```

3. The n8n editor will normally be available at http://localhost:5678 (check `docker compose logs n8n` for the exact URL shown at startup).

Notes:
- The Postgres service is configured to bind to host port `5433` in this workspace, so code or scripts that connect locally should target port `5433`.

## Database population (db-creator.py)

`db-creator.py` builds a small analytic dataset with these tables:

- `district` — store districts (small number of regions)
- `store` — stores, each assigned to a district
- `reporting_time` — weekly reporting periods
- `item` — catalog of items
- `sales` — sales rows linked to `store` and `reporting_time`

Common usage examples:

- Full recreate (drops and recreates tables) — good for a clean slate:

```zsh
python3 db-creator.py \
	--host localhost --port 5433 --db n8n_database \
	--user admin_user_db --password simplonworkflown8n \
	--force-recreate --yes-i-know
```

- Append or run with different generation parameters:

```zsh
python3 db-creator.py --help
```

Important implementation notes:
- The script supports ensuring a minimum number of sales per district per week (so analytics queries don't hit empty districts). If you need denser data, increase the `min_sales_per_district_per_week` value inside the script or via the CLI flag (if exposed).
- If you hit a `psycopg2.InterfaceError: cursor already closed` error, ensure you are running the latest `db-creator.py` — recent edits fetch required rows before closing DB cursors.

## Inspecting & verifying data (psql examples)

Use the `psql` client against the local Postgres on port 5433. Example queries used during development:

Check recent reporting periods:

```zsh
psql "host=localhost port=5433 dbname=n8n_database user=admin_user_db password=simplonworkflown8n" \
	-c "SELECT reportingperiodid, week_start, week_end FROM reporting_time ORDER BY reportingperiodid DESC LIMIT 5;"
```

Check store counts per district:

```zsh
psql "host=localhost port=5433 dbname=n8n_database user=admin_user_db password=simplonworkflown8n" \
	-c "SELECT districtid, COUNT(*) AS store_count FROM store GROUP BY districtid ORDER BY districtid;"
```

Check sales counts for the last two reporting periods grouped by district:

```zsh
psql "host=localhost port=5433 dbname=n8n_database user=admin_user_db password=simplonworkflown8n" \
	-c "SELECT st.districtid, COUNT(s.*) AS sales_count FROM sales s JOIN store st ON s.locationid = st.locationid WHERE s.reportingperiodid IN (23,24) GROUP BY st.districtid ORDER BY st.districtid;"
```

Adjust the `reportingperiodid` values to match your environment (see the `reporting_time` table results for current IDs).

## n8n community nodes & visualization (QuickChart)

n8n can load community-built nodes from special locations. This project uses the `volumes/n8n_database/nodes/` directory as the place to install community nodes so they persist across container restarts.

This workspace uses QuickChart (https://quickchart.io) for quick, serverless chart generation as a simple proof-of-workflow visualization. QuickChart returns chart images produced from a Chart.js-style JSON config and is easy to call from n8n using the HTTP Request node or a QuickChart community node if you prefer.

Notes and tips:
- To enable n8n to load community packages in local development, make sure your `.env` contains:

```text
N8N_COMMUNITY_PACKAGES_ENABLED=true
```

- If you are running n8n over HTTP (local dev) and the license UI is blocked by secure cookie behavior, set:

```text
N8N_SECURE_COOKIE=false
```

- Installing community nodes (generic): if you want to add community nodes to the persistent nodes folder, edit `volumes/n8n_database/nodes/package.json` and add the dependency, for example:

```json
{
	"dependencies": {
		"n8n-nodes-quickchart": "^1.0.0"
	}
}
```

Then run (from the repository root):

```zsh
cd volumes/n8n_database/nodes
npm install
```

- After installing community nodes, restart the n8n container so the runtime picks them up:

```zsh
docker compose restart n8n
```

- Quick example using QuickChart via the HTTP Request node:

1. In your n8n workflow, fetch or compute the data you want to visualize (SQL query -> Function node to build arrays).
2. Build a small Chart.js config JSON. Example config (bar chart):

```json
{
	"type": "bar",
	"data": {
		"labels": ["District 1","District 2","District 3"],
		"datasets": [{"label":"Sales","data":[66,88,78]}]
	}
}
```

3. Create a QuickChart URL or POST the config to the QuickChart endpoint. Example URL (Chart config URL-encoded):

```
https://quickchart.io/chart?c={...url-encoded-config...}
```

4. Use the n8n HTTP Request node to GET the URL and receive the chart PNG. You can then attach the PNG to an email, upload it to Slack, or store it in the filesystem using the n8n nodes you prefer.

Sample n8n flow (high-level):

- Execute a `Postgres` node (or `Execute Query`) to get aggregated sales per district.
- Use a `Function` node to map results into Chart.js config JSON.
- Use an `HTTP Request` node to call QuickChart with the config and receive a PNG.
- Use `Slack` / `Email` / `Write Binary File` nodes to deliver or store the chart image.

If community node packages exist but do not appear in the UI, verify:

1. The package's `package.json` contains an `n8n.nodes` entry pointing to the node implementation file.
2. The package is installed under the runtime-visible node_modules or the `volumes/n8n_database/nodes` folder and `npm install` has completed.

## Troubleshooting

- Empty districts / zero sales in analysis:
	- Cause: random store assignment produced no stores in a district. Fix: use the updated `db-creator.py` (which now distributes stores evenly) and re-run with `--force-recreate`.

- `psycopg2.InterfaceError: cursor already closed` during population:
	- Cause: attempting to use a cursor after it was closed. Fix: use the latest `db-creator.py` which fetches required rows prior to closing cursors.

- n8n community packages not visible in UI:
	- Cause: environment variable not set, package not installed in the persistent nodes folder, or image runtime mismatch.
	- Fix: set `N8N_COMMUNITY_PACKAGES_ENABLED=true` in `.env`, ensure `volumes/n8n_database/nodes/package.json` lists the package, run `npm install` in that folder, then `docker compose restart n8n`.

- License activation UI blocked locally (secure cookie):
	- Workaround for local development only: set `N8N_SECURE_COOKIE=false` in `.env`, then restart n8n. Do not use this in production.

## Development notes

- To make changes to the NodeJS community packages or add a new one:
	1. Add the dependency to `volumes/n8n_database/nodes/package.json`.
	2. Run `npm install` inside `volumes/n8n_database/nodes` on the host.
	3. Restart `n8n` container.

- If you modify `db-creator.py`, re-run the population with `--force-recreate` to see the changes applied to the database.

## Contributing

Contributions are welcome. If you open a PR, include a clear description of the change, why it is needed, and any tests or verification steps.

## License

This project is provided under the terms in the `LICENSE` file included in the repository.


## Authors
- [Michael Adebayo](https://www.github.com/MichAdebayo)
- [David Scott](https://www.github.com/Daviddavid-sudo)
- [Eliandy Rymer](https://www.github.com/EliandyDumortier)