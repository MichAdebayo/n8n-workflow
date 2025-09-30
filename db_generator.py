import argparse
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from faker import Faker

@dataclass
class DBConfig:
    dbname: str = "n8n_database.db"

    def dsn(self) -> str:
        return self.dbname


class DBCreator:
    def __init__(self, dbconfig: DBConfig, seed: int = 42):
        self.db = dbconfig
        self.seed = seed
        self.fake = Faker()
        Faker.seed(seed)
        random.seed(seed)

    def connect(self):
        return sqlite3.connect(self.db.dsn())

    def create_tables(self):
        sql = [
            """
            CREATE TABLE IF NOT EXISTS district (
                districtid INTEGER PRIMARY KEY AUTOINCREMENT,
                businessunitid INTEGER,
                districtname TEXT,
                dm TEXT,
                dm_pic TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS store (
                locationid INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                city TEXT,
                postalcode TEXT,
                districtid INTEGER,
                FOREIGN KEY(districtid) REFERENCES district(districtid)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reporting_time (
                reportingperiodid INTEGER PRIMARY KEY AUTOINCREMENT,
                fiscalyear INTEGER,
                fiscalmonth INTEGER,
                month_name TEXT,
                period INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS item (
                itemid INTEGER PRIMARY KEY AUTOINCREMENT,
                familyname TEXT,
                category TEXT,
                segment TEXT,
                buyer TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS sales (
                salesid INTEGER PRIMARY KEY AUTOINCREMENT,
                itemid INTEGER,
                locationid INTEGER,
                reportingperiodid INTEGER,
                scenarioid INTEGER,
                sum_grossmarginamount REAL,
                sum_regular_sales_dollars REAL,
                sum_regular_sales_units INTEGER,
                sum_markdown_sales_dollars REAL,
                sum_markdown_sales_units INTEGER,
                FOREIGN KEY(itemid) REFERENCES item(itemid),
                FOREIGN KEY(locationid) REFERENCES store(locationid),
                FOREIGN KEY(reportingperiodid) REFERENCES reporting_time(reportingperiodid)
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
        for _ in range(n):
            rows.append(
                {
                    "businessunitid": random.randint(1, 10),
                    "districtname": self.fake.company() + " District",
                    "dm": self.fake.name(),
                    "dm_pic": self.fake.email(),
                }
            )
        return pd.DataFrame(rows)

    def generate_stores(self, n: int = 50, districts: pd.DataFrame | None = None) -> pd.DataFrame:
        rows = []
        district_ids = list(range(1, len(districts) + 1)) if districts is not None else None
        for _ in range(n):
            rows.append(
                {
                    "name": f"{self.fake.company()} Store",
                    "city": self.fake.city(),
                    "postalcode": self.fake.postcode(),
                    "districtid": random.choice(district_ids) if district_ids else None,
                }
            )
        return pd.DataFrame(rows)

    def generate_time(self, months: int = 24, start: Optional[datetime] = None) -> pd.DataFrame:
        if start is None:
            today = datetime.today()
            start = datetime(today.year, today.month, 1) - timedelta(days=30 * (months - 1))

        rows = []
        cur = start
        for i in range(months):
            rows.append(
                {
                    "fiscalyear": cur.year,
                    "fiscalmonth": cur.month,
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
        for _ in range(n):
            rows.append(
                {
                    "familyname": self.fake.word().title(),
                    "category": random.choice(categories),
                    "segment": random.choice(segments),
                    "buyer": self.fake.name(),
                }
            )
        return pd.DataFrame(rows)

    def generate_sales(self, n_rows: int, items: pd.DataFrame, stores: pd.DataFrame, periods: pd.DataFrame) -> pd.DataFrame:
        rows = []
        item_count = len(items)
        store_count = len(stores)
        period_count = len(periods)

        for _ in range(n_rows):
            item_idx = random.randint(1, item_count)
            store_idx = random.randint(1, store_count)
            period_idx = random.randint(1, period_count)

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

    def df_to_sqlite(self, table: str, df: pd.DataFrame):
        if df.empty:
            return
        conn = self.connect()
        try:
            df.to_sql(table, conn, if_exists="append", index=False)
        finally:
            conn.close()

    def run(self, districts=5, stores=50, items=200, months=24, sales_rows=5000):
        print("[INFO] Creating tables...")
        self.create_tables()

        print("[INFO] Generating districts...")
        df_dist = self.generate_districts(districts)
        self._reset_table("district")
        self.df_to_sqlite("district", df_dist)

        print("[INFO] Generating stores...")
        df_stores = self.generate_stores(stores, districts=df_dist)
        self._reset_table("store")
        self.df_to_sqlite("store", df_stores)

        print("[INFO] Generating reporting periods...")
        df_time = self.generate_time(months=months)
        self._reset_table("reporting_time")
        self.df_to_sqlite("reporting_time", df_time)

        print("[INFO] Generating items...")
        df_items = self.generate_items(items)
        self._reset_table("item")
        self.df_to_sqlite("item", df_items)

        print(f"[INFO] Generating sales rows: {sales_rows}...")
        df_sales = self.generate_sales(sales_rows, df_items, df_stores, df_time)
        self._reset_table("sales")
        self.df_to_sqlite("sales", df_sales)

        print("[DONE] Data generation and load finished.")

    def _reset_table(self, table: str):
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table};")
            cur.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}';")
            conn.commit()
            cur.close()
        finally:
            conn.close()


def parse_args():
        p = argparse.ArgumentParser()
        p.add_argument("--db", default="n8n_database.db")
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--districts", type=int, default=5)
        p.add_argument("--stores", type=int, default=50)
        p.add_argument("--items", type=int, default=200)
        p.add_argument("--months", type=int, default=24)
        p.add_argument("--sales-rows", type=int, default=5000)
        return p.parse_args()

def main():
    args = parse_args()
    cfg = DBConfig(dbname=args.db)
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
