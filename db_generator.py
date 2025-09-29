#!/usr/bin/env python3
"""db-creator.py

Generate synthetic relational data (District, Store, Time, Item, Sales)
and load it into a PostgreSQL database. Uses Faker + pandas + psycopg2.

Usage (example):
  python3 db-creator.py --host localhost --port 5432 --db n8n_database \
    --user admin_user_db --password secret --districts 5 --stores 50 --items 200 --months 24 --sales-rows 5000

The script creates tables if they do not exist and uses COPY for efficient bulk
loads. Set --seed for reproducible output.
"""

from __future__ import annotations

import argparse
import io
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import psycopg2
from faker import Faker


@dataclass
class DBConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "n8n_database"
    user: str = "admin_user_db"
    password: str = "password"

    def dsn(self) -> str:
        return f"host={self.host} port={self.port} dbname={self.dbname} user={self.user} password={self.password}"


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
        return psycopg2.connect(self.db.dsn())

    def create_tables(self):
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
                period INTEGER
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
                sum_markdown_sales_units INTEGER
            );
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

    def generate_districts(self, n: int = 5) -> pd.DataFrame:
        rows = []
        for i in range(n):
            rows.append(
                {
                    "businessunitid": random.randint(1, 10),
                    "districtname": self.fake.company() + " District",
                    "dm": self.fake.name(),
                    "dm_pic": self.fake.email(),
                }
            )
        df = pd.DataFrame(rows)
        # let Postgres assign districtid (serial), but keep index for relations if needed
        return df

    def generate_stores(
        self, n: int = 50, districts: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        rows = []
        district_ids = (
            list(range(1, len(districts) + 1)) if districts is not None else None
        )
        for i in range(n):
            rows.append(
                {
                    "name": f"{self.fake.company()} Store",
                    "city": self.fake.city(),
                    "postalcode": self.fake.postcode(),
                    "districtid": random.choice(district_ids) if district_ids else None,
                }
            )
        return pd.DataFrame(rows)

    def generate_time(
        self, months: int = 24, start: Optional[datetime] = None
    ) -> pd.DataFrame:
        if start is None:
            # default to start at beginning of current month minus months
            today = datetime.today()
            start = datetime(today.year, today.month, 1) - timedelta(
                days=30 * (months - 1)
            )

        rows = []
        cur = start
        for i in range(months):
            fiscalyear = cur.year
            fiscalmonth = cur.month
            rows.append(
                {
                    "fiscalyear": fiscalyear,
                    "fiscalmonth": fiscalmonth,
                    "month_name": cur.strftime("%B"),
                    "period": i + 1,
                }
            )
            # advance one month
            if cur.month == 12:
                cur = datetime(cur.year + 1, 1, 1)
            else:
                cur = datetime(cur.year, cur.month + 1, 1)

        return pd.DataFrame(rows)

    def generate_items(self, n: int = 200) -> pd.DataFrame:
        categories = ["Grocery", "Electronics", "Apparel", "Home", "Beauty"]
        segments = ["A", "B", "C"]
        rows = []
        for i in range(n):
            rows.append(
                {
                    "familyname": self.fake.word().title(),
                    "category": random.choice(categories),
                    "segment": random.choice(segments),
                    "buyer": self.fake.name(),
                }
            )
        return pd.DataFrame(rows)

    def generate_sales(
        self,
        n_rows: int,
        items: pd.DataFrame,
        stores: pd.DataFrame,
        periods: pd.DataFrame,
    ) -> pd.DataFrame:
        rows = []
        item_count = len(items)
        store_count = len(stores)
        period_count = len(periods)

        for _ in range(n_rows):
            item_idx = random.randint(1, item_count)
            store_idx = random.randint(1, store_count)
            period_idx = random.randint(1, period_count)

            # base sales depending on category/segment could be added; keep simple
            units = max(0, int(random.gauss(20, 10)))
            price = round(random.uniform(5, 200), 2)
            regular_dollars = round(units * price, 2)
            markdown_units = max(0, int(random.gauss(2, 3)))
            markdown_dollars = round(markdown_units * price * 0.5, 2)
            gross_margin = round(regular_dollars * random.uniform(0.15, 0.4), 2)

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
                }
            )

        return pd.DataFrame(rows)

    def df_to_postgres(self, table: str, df: pd.DataFrame):
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
        stores=50,
        items=200,
        months=24,
        sales_rows=5000,
    ):
        # create schema
        print("[INFO] Creating tables...")
        self.create_tables()

        print("[INFO] Generating districts...")
        df_dist = self.generate_districts(districts)
        # insert districts and fetch assigned ids
        # We will insert using COPY but need to preserve serial ids; perform COPY into a temp table
        # Simpler: insert districts one-by-one to get ids
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE district RESTART IDENTITY CASCADE;")
            for _, row in df_dist.iterrows():
                cur.execute(
                    "INSERT INTO district (businessunitid, districtname, dm, dm_pic) VALUES (%s,%s,%s,%s)",
                    (row.businessunitid, row.districtname, row.dm, row.dm_pic),
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()

        # Stores
        print("[INFO] Generating stores...")
        df_stores = self.generate_stores(stores, districts=df_dist)
        # ensure store ids start fresh
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE store RESTART IDENTITY CASCADE;")
            # use COPY for stores
            self.df_to_postgres("store", df_stores)
            cur.close()
        finally:
            conn.close()

        # Time
        print("[INFO] Generating reporting periods (time)...")
        df_time = self.generate_time(months=months)
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE reporting_time RESTART IDENTITY CASCADE;")
            self.df_to_postgres("reporting_time", df_time)
            cur.close()
        finally:
            conn.close()

        # Items
        print("[INFO] Generating items...")
        df_items = self.generate_items(n=items)
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE item RESTART IDENTITY CASCADE;")
            self.df_to_postgres("item", df_items)
            cur.close()
        finally:
            conn.close()

        # Sales
        print(f"[INFO] Generating sales rows: {sales_rows}...")
        df_sales = self.generate_sales(sales_rows, df_items, df_stores, df_time)
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE sales RESTART IDENTITY CASCADE;")
            self.df_to_postgres("sales", df_sales)
            cur.close()
        finally:
            conn.close()

        print("[DONE] Data generation and load finished.")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--db", default="n8n_database")
    p.add_argument("--user", default="admin_user_db")
    p.add_argument("--password", default="password")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--districts", type=int, default=5)
    p.add_argument("--stores", type=int, default=50)
    p.add_argument("--items", type=int, default=200)
    p.add_argument("--months", type=int, default=24)
    p.add_argument("--sales-rows", type=int, default=5000)
    return p.parse_args()


def main():
    args = parse_args()
    cfg = DBConfig(
        host=args.host,
        port=args.port,
        dbname=args.db,
        user=args.user,
        password=args.password,
    )
    creator = DBCreator(cfg, seed=args.seed)
    creator.run(
        districts=args.districts,
        stores=args.stores,
        items=args.items,
        months=args.months,
        sales_rows=args.sales_rows,
    )


if __name__ == "__main__":
    main()
