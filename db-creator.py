#!/usr/bin/env python3
"""
db-creator.py

Generate synthetic relational data (District, Store, Time, Item, Sales)
and load it into a PostgreSQL database running in Docker Compose.
Uses Faker + pandas + psycopg2.

Recommended usage (with Docker Compose):
    # Run this from your project root, with the database running as 'db' service
    docker compose exec db python3 /workspace/db-creator.py --host db --port 5432 --db n8n_database --user admin_user_db --password <your_password>

If you want to run from your host, use --host localhost and ensure port 5432 is mapped and accessible.

The script creates tables if they do not exist and uses COPY for efficient bulk loads. Set --seed for reproducible output.
"""

from __future__ import annotations

import argparse
import io
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


import pandas as pd
import psycopg2

from faker import Faker


@dataclass
class DBConfig:
    """Database connection configuration."""

    # Default to 'localhost' for local development
    host: str = os.getenv("POSTGRES_HOST", "localhost")
    port: int = int(os.getenv("DB_POSTGRESDB_PORT", 5433))
    dbname: str = os.getenv("POSTGRES_DB", "n8n_database")
    user: str = os.getenv("POSTGRES_USER", "admin_user_db")
    password: str = os.getenv("POSTGRES_PASSWORD", "password")

    def dsn(self) -> str:
        # Always use password for Docker Compose
        dsn = f"host={self.host} port={self.port} dbname={self.dbname} user={self.user} password={self.password}"
        return dsn


class DBCreator:
    """Create synthetic dataframes and load them into Postgres.

    Methods:
      - create_tables(): create schema if not exists
      - generate_*(): produce pandas DataFrames
      - df_to_postgres(): fast COPY-based upload
      - run(): orchestrate generation and upload
    """

    def __init__(self, dbconfig: DBConfig, seed: int = 42):
        self.db = dbconfig
        self.seed = seed
        self.fake = Faker()
        Faker.seed(seed)
        random.seed(seed)

    def connect(self):
        """Connect to the Postgres database using the provided configuration."""
        try:
            return psycopg2.connect(self.db.dsn())
        except psycopg2.OperationalError as e:
            # Provide an actionable error message to help troubleshoot common Docker/Postgres
            import sys

            extra = ""
            if self.db.dbname == "postgres":
                extra = (
                    "\n[WARNING] You are trying to connect to the default 'postgres' database. "
                    "For this project, you should use the 'n8n_database' database. "
                    "Update your command or .env to use --db n8n_database.\n"
                )

            msg = (
                f"Failed to connect to Postgres at {self.db.host}:{self.db.port} as user '{self.db.user}'.\n"
                f"Underlying error: {e}\n\n"
                "Common causes:\n"
                "  - The Postgres server is not running or not reachable on that host/port.\n"
                "  - The Postgres data directory was initialized with different POSTGRES_USER, so the requested role does not exist.\n\n"
                "Quick fixes:\n"
                "  1) Create the missing role and database (safe, keeps existing data):\n"
                "     If you're using Docker Compose from this project, run:\n"
                "\n"
                "     docker compose exec db psql -U postgres -c \"CREATE ROLE {user} WITH LOGIN PASSWORD '{pwd}';\"\n"
                '     docker compose exec db psql -U postgres -c "CREATE DATABASE {db} OWNER {user};"\n'
                "\n"
                "     Replace {user} and {pwd} with your POSTGRES_USER and POSTGRES_PASSWORD from .env.\n\n"
                "  2) Reinitialize Postgres so the container creates the user from .env (DESTROYS DATA):\n"
                "     docker compose down\n"
                "     rm -rf ./volumes/postgres_data/*\n"
                "     docker compose up -d --build\n\n"
                "Choose option 1 to preserve data.\n"
                f"{extra}"
            ).format(user=self.db.user, pwd=self.db.password, db=self.db.dbname)

            print(msg, file=sys.stderr)
            raise

    def create_tables(self):
        """Create the necessary tables if they do not already exist."""
        sql = [
            """
            CREATE TABLE IF NOT EXISTS district (
                districtid SERIAL PRIMARY KEY,
                businessunitid INTEGER,
                districtname TEXT,
                dm TEXT,
                dm_pic TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS store (
                locationid SERIAL PRIMARY KEY,
                name TEXT,
                city TEXT,
                postalcode TEXT,
                districtid INTEGER REFERENCES district(districtid)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reporting_time (
                reportingperiodid SERIAL PRIMARY KEY,
                fiscalyear INTEGER,
                fiscalmonth INTEGER,
                month_name TEXT,
                period INTEGER,
                -- weekly fields to support weekly rollups
                week_start DATE,
                week_end DATE,
                week_number INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS item (
                itemid SERIAL PRIMARY KEY,
                familyname TEXT,
                category TEXT,
                segment TEXT,
                buyer TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS sales (
                salesid SERIAL PRIMARY KEY,
                itemid INTEGER REFERENCES item(itemid),
                locationid INTEGER REFERENCES store(locationid),
                reportingperiodid INTEGER REFERENCES reporting_time(reportingperiodid),
                scenarioid INTEGER,
                sum_grossmarginamount NUMERIC,
                sum_regular_sales_dollars NUMERIC,
                sum_regular_sales_units INTEGER,
                sum_markdown_sales_dollars NUMERIC,
                sum_markdown_sales_units INTEGER,
                -- convenience columns for easier reporting/Power BI
                total_revenue NUMERIC,
                total_gross_margin NUMERIC
            );
            """,
            # additional helpful indexes for faster joins/aggregations
            """
            CREATE INDEX IF NOT EXISTS idx_sales_reportingperiodid ON sales(reportingperiodid);
            CREATE INDEX IF NOT EXISTS idx_sales_locationid ON sales(locationid);
            CREATE INDEX IF NOT EXISTS idx_sales_itemid ON sales(itemid);
            """,
        ]

        conn = self.connect()
        try:
            cur = conn.cursor()
            for s in sql:
                cur.execute(s)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def drop_tables(self):
        """Destructively drop the data tables so they can be recreated.

        This is useful for development/demo runs when you want a clean schema
        and data. Use with care: this will delete data in the listed tables.
        """
        sql = """
        DROP TABLE IF EXISTS sales, item, reporting_time, store, district CASCADE;
        """
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def generate_districts(self, n: int = 5) -> pd.DataFrame:
        rows = []
        rows.extend(
            {
                "businessunitid": random.randint(1, 10),
                "districtname": self.fake.company() + " District",
                "dm": self.fake.name(),
                "dm_pic": self.fake.email(),
            }
            for _ in range(n)
        )
        df = pd.DataFrame(rows)
        # let Postgres assign districtid (serial), but keep index for relations if needed
        return df

    def generate_stores(
        self, n: int = 50, districts: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Generate a DataFrame of stores with fake data."""
        rows = []
        # If districts DataFrame includes real districtid column, use it; otherwise
        # fallback to generating sequential ids 1..n (suitable for fresh DB).
        district_ids = []
        if districts is not None:
            if "districtid" in districts.columns:
                district_ids = districts["districtid"].tolist()
            else:
                district_ids = list(range(1, len(districts) + 1))

        # If we have district ids, distribute stores evenly so every district gets coverage
        if district_ids:
            num_districts = len(district_ids)
            base = n // num_districts
            rem = n % num_districts
            # create a list of district assignments
            assignments = []
            for did in district_ids:
                assignments.extend([did] * base)
            # distribute the remainder
            for i in range(rem):
                assignments.append(district_ids[i % num_districts])
            # if assignments length differs due to rounding, trim or pad
            if len(assignments) < n:
                assignments.extend(
                    [random.choice(district_ids) for _ in range(n - len(assignments))]
                )
            elif len(assignments) > n:
                assignments = assignments[:n]

            for i in range(n):
                rows.append(
                    {
                        "name": f"{self.fake.company()} Store",
                        "city": self.fake.city(),
                        "postalcode": self.fake.postcode(),
                        "districtid": assignments[i],
                    }
                )
        else:
            # fallback: no district info available
            rows.extend(
                {
                    "name": f"{self.fake.company()} Store",
                    "city": self.fake.city(),
                    "postalcode": self.fake.postcode(),
                    "districtid": None,
                }
                for _ in range(n)
            )
        return pd.DataFrame(rows)

    def generate_time(
        self, months: int = 24, start: Optional[datetime] = None
    ) -> pd.DataFrame:
        """Generate a DataFrame of reporting periods with weekly granularity."""
        # For weekly reporting the `months` parameter is treated as number of weeks.
        weeks = months
        if start is None:
            # default: align to the Monday of (weeks) ago
            today = datetime.now()
            start = today - timedelta(weeks=weeks)
            start = start - timedelta(days=start.weekday())

        rows = []
        cur = start
        for i in range(weeks):
            fiscalyear = cur.year
            fiscalmonth = cur.month
            week_number = cur.isocalendar()[1]
            rows.append(
                {
                    "fiscalyear": fiscalyear,
                    "fiscalmonth": fiscalmonth,
                    "month_name": cur.strftime("%B"),
                    "period": i + 1,
                    "week_start": cur.date(),
                    "week_end": (cur + timedelta(days=6)).date(),
                    "week_number": week_number,
                }
            )
            cur = cur + timedelta(weeks=1)

        return pd.DataFrame(rows)

    def generate_items(self, n: int = 200) -> pd.DataFrame:
        """Generate a DataFrame of items with fake data."""
        categories = ["Grocery", "Electronics", "Apparel", "Home", "Beauty"]
        segments = ["A", "B", "C"]
        rows = []
        rows.extend(
            {
                "familyname": self.fake.word().title(),
                "category": random.choice(categories),
                "segment": random.choice(segments),
                "buyer": self.fake.name(),
            }
            for _ in range(n)
        )
        return pd.DataFrame(rows)

    def generate_sales(
        self,
        n_rows: int,
        items,  # can be pd.DataFrame or list of ids
        stores,  # can be pd.DataFrame or list of ids
        periods,  # can be pd.DataFrame or list of ids
        districts: Optional[list] = None,
        store_district_map: Optional[dict] = None,
        min_sales_per_district_per_week: int = 10,
    ) -> pd.DataFrame:
        """
        Generate sales so that every district has at least min_sales_per_district_per_week sales per week.
        """
        rows = []

        # Normalize inputs to id lists
        if isinstance(items, pd.DataFrame):
            if "itemid" in items.columns:
                item_ids = items["itemid"].tolist()
            else:
                item_ids = list(range(1, len(items) + 1))
        elif isinstance(items, list):
            item_ids = items
        else:
            raise ValueError("items must be a DataFrame or list of ids")

        if isinstance(stores, pd.DataFrame):
            if "locationid" in stores.columns:
                store_ids = stores["locationid"].tolist()
            else:
                store_ids = list(range(1, len(stores) + 1))
        elif isinstance(stores, list):
            store_ids = stores
        else:
            raise ValueError("stores must be a DataFrame or list of ids")

        if isinstance(periods, pd.DataFrame):
            if "reportingperiodid" in periods.columns:
                period_ids = periods["reportingperiodid"].tolist()
            else:
                period_ids = list(range(1, len(periods) + 1))
        elif isinstance(periods, list):
            period_ids = periods
        else:
            raise ValueError("periods must be a DataFrame or list of ids")

        # Build store->district map if not provided
        if store_district_map is None:
            store_district_map = {}
            # You must pass stores as a DataFrame with districtid column for this to work
            if isinstance(stores, pd.DataFrame) and "districtid" in stores.columns:
                for _, row in stores.iterrows():
                    store_district_map[row["locationid"]] = row["districtid"]
            else:
                # fallback: assign all stores to district 1
                for sid in store_ids:
                    store_district_map[sid] = 1

        # Get all unique districts
        if districts is None:
            districts = list(set(store_district_map.values()))

        # For each week and each district, generate at least min_sales_per_district_per_week sales
        for period_id in period_ids:
            for district_id in districts:
                # Get all stores in this district
                stores_in_district = [
                    sid for sid, did in store_district_map.items() if did == district_id
                ]
                if not stores_in_district:
                    continue
                for _ in range(min_sales_per_district_per_week):
                    item_idx = random.choice(item_ids)
                    store_idx = random.choice(stores_in_district)
                    # base sales depending on category/segment could be added; keep simple
                    units = max(0, int(random.gauss(20, 10)))
                    price = round(random.uniform(5, 200), 2)
                    regular_dollars = round(units * price, 2)
                    markdown_units = max(0, int(random.gauss(2, 3)))
                    markdown_dollars = round(markdown_units * price * 0.5, 2)
                    gross_margin = round(regular_dollars * random.uniform(0.15, 0.4), 2)
                    total_revenue = round(regular_dollars + markdown_dollars, 2)
                    total_gross_margin = round(gross_margin, 2)
                    rows.append(
                        {
                            "itemid": item_idx,
                            "locationid": store_idx,
                            "reportingperiodid": period_id,
                            "scenarioid": random.randint(1, 3),
                            "sum_grossmarginamount": gross_margin,
                            "sum_regular_sales_dollars": regular_dollars,
                            "sum_regular_sales_units": units,
                            "sum_markdown_sales_dollars": markdown_dollars,
                            "sum_markdown_sales_units": markdown_units,
                            "total_revenue": total_revenue,
                            "total_gross_margin": total_gross_margin,
                        }
                    )

        # Optionally, add more random sales to reach n_rows if needed
        extra_needed = max(0, n_rows - len(rows))
        for _ in range(extra_needed):
            item_idx = random.choice(item_ids)
            store_idx = random.choice(store_ids)
            period_idx = random.choice(period_ids)
            units = max(0, int(random.gauss(20, 10)))
            price = round(random.uniform(5, 200), 2)
            regular_dollars = round(units * price, 2)
            markdown_units = max(0, int(random.gauss(2, 3)))
            markdown_dollars = round(markdown_units * price * 0.5, 2)
            gross_margin = round(regular_dollars * random.uniform(0.15, 0.4), 2)
            total_revenue = round(regular_dollars + markdown_dollars, 2)
            total_gross_margin = round(gross_margin, 2)
            rows.append(
                {
                    "itemid": item_idx,
                    "locationid": store_idx,
                    "reportingperiodid": period_idx,
                    "scenarioid": random.randint(1, 3),
                    "sum_grossmarginamount": gross_margin,
                    "sum_regular_sales_dollars": regular_dollars,
                    "sum_regular_sales_units": units,
                    "sum_markdown_sales_dollars": markdown_dollars,
                    "sum_markdown_sales_units": markdown_units,
                    "total_revenue": total_revenue,
                    "total_gross_margin": total_gross_margin,
                }
            )

        return pd.DataFrame(rows)

    def df_to_postgres(self, table: str, df: pd.DataFrame):
        """Use COPY via StringIO to efficiently bulk load a DataFrame into Postgres."""
        if df.empty:
            return

        # use COPY via StringIO for performance; ensure column order
        cols = list(df.columns)
        sio = io.StringIO()
        df.to_csv(sio, index=False, header=True)
        sio.seek(0)

        conn = self.connect()
        try:
            cur = conn.cursor()
            sql = f"COPY {table}({', '.join(cols)}) FROM STDIN WITH CSV HEADER"
            cur.copy_expert(sql, sio)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def run(
        self,
        districts=5,
        stores=20,
        items=200,
        months=24,
        sales_rows=5000,
        force_recreate: bool = False,
    ):

        # Orchestrate the data generation and loading process.
        if force_recreate:
            print("[WARN] --force-recreate provided: dropping existing tables...")
            self.drop_tables()
        # create schema
        print("[INFO] Creating tables...")
        self.create_tables()

        print("[INFO] Checking and populating districts...")
        df_dist = None
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM district;")
            result = cur.fetchone()
            if result is None:
                print(
                    "[WARN] Could not check district table row count. Skipping population."
                )
            elif result[0] == 0:
                print("[INFO] Populating districts table...")
                df_dist = self.generate_districts(districts)
                for _, row in df_dist.iterrows():
                    cur.execute(
                        "INSERT INTO district (businessunitid, districtname, dm, dm_pic) VALUES (%s,%s,%s,%s)",
                        (row.businessunitid, row.districtname, row.dm, row.dm_pic),
                    )
                conn.commit()
                # Re-fetch districts with their assigned serial IDs for downstream FK use
                cur.execute(
                    "SELECT districtid, businessunitid, districtname, dm, dm_pic FROM district ORDER BY districtid;"
                )
                rows = cur.fetchall()
                df_dist = pd.DataFrame(
                    rows,
                    columns=[
                        "districtid",
                        "businessunitid",
                        "districtname",
                        "dm",
                        "dm_pic",
                    ],
                )
            else:
                print(
                    f"[INFO] Skipping districts: already populated with {result[0]} rows."
                )
                # Fetch districts from DB for downstream use
                cur.execute(
                    "SELECT districtid, businessunitid, districtname, dm, dm_pic FROM district ORDER BY districtid;"
                )
                rows = cur.fetchall()
                df_dist = pd.DataFrame(
                    rows,
                    columns=[
                        "districtid",
                        "businessunitid",
                        "districtname",
                        "dm",
                        "dm_pic",
                    ],
                )
            cur.close()
        finally:
            conn.close()

        print("[INFO] Checking and populating stores...")
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM store;")
            result = cur.fetchone()
            if result is None:
                print(
                    "[WARN] Could not check store table row count. Skipping population."
                )
            elif result[0] == 0:
                print("[INFO] Populating stores table...")
                df_stores = self.generate_stores(stores, districts=df_dist)
                print(f"[DEBUG] Store DataFrame shape: {df_stores.shape}")
                print(f"[DEBUG] First 5 rows:\n{df_stores.head()}")
                for idx, (_, row) in enumerate(df_stores.iterrows()):
                    cur.execute(
                        "INSERT INTO store (name, city, postalcode, districtid) VALUES (%s, %s, %s, %s)",
                        (row.name, row.city, row.postalcode, row.districtid),
                    )
                    print(
                        f"[DEBUG] Inserted store {idx+1}/{len(df_stores)}: {row.name}"
                    )
                conn.commit()
            else:
                print(
                    f"[INFO] Skipping stores: already populated with {result[0]} rows."
                )
            cur.close()
        finally:
            conn.close()

        # Time
        print("[INFO] Checking and populating reporting_time...")
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM reporting_time;")
            result = cur.fetchone()
            if result is None:
                print(
                    "[WARN] Could not check reporting_time table row count. Skipping population."
                )
            elif result[0] == 0:
                print("[INFO] Populating reporting_time table...")
                df_time = self.generate_time(months=months)
                self.df_to_postgres("reporting_time", df_time)
            else:
                print(
                    f"[INFO] Skipping reporting_time: already populated with {result[0]} rows."
                )
            cur.close()
        finally:
            conn.close()

        # Items
        print("[INFO] Checking and populating items...")
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM item;")
            result = cur.fetchone()
            if result is None:
                print(
                    "[WARN] Could not check item table row count. Skipping population."
                )
            elif result[0] == 0:
                print("[INFO] Populating item table...")
                df_items = self.generate_items(n=items)
                self.df_to_postgres("item", df_items)
            else:
                print(f"[INFO] Skipping item: already populated with {result[0]} rows.")
            cur.close()
        finally:
            conn.close()

        # Sales
        print("[INFO] Checking and populating sales...")
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sales;")
            result = cur.fetchone()
            if result is None:
                print(
                    "[WARN] Could not check sales table row count. Skipping population."
                )
            elif result[0] == 0:
                print(f"[INFO] Populating sales table with {sales_rows} rows...")
                # Use actual IDs from item, store, reporting_time to ensure referential integrity
                conn2 = self.connect()
                try:
                    cur2 = conn2.cursor()
                    cur2.execute("SELECT itemid FROM item;")
                    item_ids = [r[0] for r in cur2.fetchall()]
                    cur2.execute("SELECT locationid FROM store;")
                    store_ids = [r[0] for r in cur2.fetchall()]
                    cur2.execute("SELECT reportingperiodid FROM reporting_time;")
                    period_ids = [r[0] for r in cur2.fetchall()]
                    # Fetch stores as DataFrame for mapping BEFORE closing cursor
                    cur2.execute("SELECT locationid, districtid FROM store;")
                    store_rows = cur2.fetchall()
                    store_district_map = {row[0]: row[1] for row in store_rows}
                    district_ids = list(set(store_district_map.values()))
                    cur2.close()
                finally:
                    conn2.close()

                df_sales = self.generate_sales(
                    sales_rows,
                    item_ids,
                    store_ids,
                    period_ids,
                    districts=district_ids,
                    store_district_map=store_district_map,
                    min_sales_per_district_per_week=10,
                )
                # Ensure column order matches table (we'll rely on COPY to map by names)
                self.df_to_postgres("sales", df_sales)
            else:
                print(
                    f"[INFO] Skipping sales: already populated with {result[0]} rows."
                )
            cur.close()
        finally:
            conn.close()

        print("[DONE] Data generation and load finished.")


def parse_args():
    """Parse command-line arguments with sensible defaults from environment variables."""
    p = argparse.ArgumentParser()
    # Load defaults from environment variables, fallback to safe values
    p.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("POSTGRES_PORT", "5433")))
    p.add_argument("--db", default=os.getenv("POSTGRES_DB", "n8n_database"))
    p.add_argument("--user", default=os.getenv("POSTGRES_USER", "admin_user_db"))
    p.add_argument("--password", default=os.getenv("POSTGRES_PASSWORD", "password"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--districts", type=int, default=5)
    p.add_argument("--stores", type=int, default=20)
    p.add_argument("--items", type=int, default=200)
    p.add_argument("--months", type=int, default=24)
    p.add_argument("--sales-rows", type=int, default=5000)
    p.add_argument(
        "--force-recreate",
        action="store_true",
        help="Drop and recreate the data tables before populating (destructive)",
    )
    p.add_argument(
        "--yes-i-know",
        action="store_true",
        help="Bypass confirmation for --force-recreate (use with care)",
    )
    return p.parse_args()


def main():
    """Main entry point for the script."""
    args = parse_args()
    cfg = DBConfig(
        host=args.host,
        port=args.port,
        dbname=args.db,
        user=args.user,
        password=args.password,
    )
    creator = DBCreator(cfg, seed=args.seed)
    # If force_recreate is requested but not explicitly confirmed, ask the user
    if args.force_recreate and not args.yes_i_know:
        print("[WARNING] --force-recreate will DROP existing tables and DATA.")
        print("Type DROP (all-caps) to confirm, or Ctrl-C to cancel:")
        try:
            resp = input().strip()
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            return
        if resp != "DROP":
            print("Confirmation failed — aborting.")
            return

    creator.run(
        districts=args.districts,
        stores=args.stores,
        items=args.items,
        months=args.months,
        sales_rows=args.sales_rows,
        force_recreate=args.force_recreate,
    )


if __name__ == "__main__":
    main()
