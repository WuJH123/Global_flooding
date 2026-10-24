from pathlib import Path
import pyarrow.parquet as pq
import json

SRC = Path(
    r"E:\数据集\Groundsource_全球城市内涝点位\groundsource_2026.parquet"
)

pf = pq.ParquetFile(SRC)

metadata = pf.metadata.metadata or {}

print("Metadata keys:")
for key in metadata.keys():
    print(key)

print("\n" + "=" * 80)
print("GEO METADATA")
print("=" * 80)

geo_raw = metadata.get(b"geo")

if geo_raw is None:
    print("No 'geo' metadata found.")
else:
    geo = json.loads(
        geo_raw.decode("utf-8")
    )

    print(
        json.dumps(
            geo,
            indent=2,
            ensure_ascii=False
        )
    )