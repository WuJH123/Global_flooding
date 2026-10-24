import pyarrow.parquet as pq

path = r"E:\数据集\Groundsource_全球城市内涝点位\groundsource_2026.parquet"

pf = pq.ParquetFile(path)

print("Rows:", pf.metadata.num_rows)
print("Row groups:", pf.num_row_groups)
print("Schema:")
print(pf.schema)

df = pf.read_row_group(0).to_pandas().head(10)
print(df)
print(df.dtypes)