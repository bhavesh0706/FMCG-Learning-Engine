
import os
import pandas as pd
from sqlalchemy import URL, create_engine, inspect

# Configure these environment variables in PowerShell first.
required = ["MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
missing = [key for key in required if not os.getenv(key)]

if missing:
    raise RuntimeError(
        "Set these environment variables first: " + ", ".join(missing)
    )

url = URL.create(
    "mysql+pymysql",
    username=os.environ["MYSQL_USER"],
    password=os.environ["MYSQL_PASSWORD"],
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    database=os.environ["MYSQL_DATABASE"],
)

engine = create_engine(url)
output_file = "FMCG_MySQL_Export.xlsx"

with engine.connect() as connection:
    inspector = inspect(connection)
    tables = inspector.get_table_names()

    if not tables:
        raise RuntimeError("No tables found in this MySQL database.")

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        used_names = set()

        for table in tables:
            # Excel worksheet names must be unique and at most 31 chars.
            base = table[:31]
            sheet = base
            suffix = 1

            while sheet.lower() in used_names:
                ending = f"_{suffix}"
                sheet = base[:31 - len(ending)] + ending
                suffix += 1

            used_names.add(sheet.lower())
            df = pd.read_sql_query(
                f"SELECT * FROM `{table.replace('`', '``')}`",
                connection,
            )
            df.to_excel(writer, sheet_name=sheet, index=False)
            print(f"Exported {table}: {len(df)} rows")

print(f"\nExcel export created: {os.path.abspath(output_file)}")
