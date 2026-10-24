from pathlib import Path
import traceback
import xarray as xr

files = {
    "CaMa-Flood": (
        r"E:\数据集\全球河流洪水预测：淹没面积"
        r"\cama-flood_classic_gfdl-esm4_historical_2015soc_default_fldfrcmax_2yr_15arcmin_global_annual_1850_2014.nc"
    ),
    "CRU": (
        r"E:\数据集\CRU TS v4.09全球降水数据集"
        r"\cru_ts4.09.1901.2024.pre.dat.nc"
    ),
}

for name, filename in files.items():
    p = Path(filename)
    print(f"\n{'=' * 60}")
    print(name)
    print("Exists:", p.is_file())
    print("Path:", p)

    if not p.is_file():
        continue

    print("Size GB:", round(p.stat().st_size / 1e9, 3))

    for engine in ("netcdf4", "h5netcdf", "scipy"):
        try:
            with xr.open_dataset(
                p,
                engine=engine,
                decode_times=False,
            ) as ds:
                print("SUCCESS:", engine)
                print("Dimensions:", dict(ds.sizes))
                print("Variables:", list(ds.data_vars))
                for variable in ds.data_vars:
                    print(variable, ds[variable].attrs.get("units"))
                break
        except Exception as exc:
            print("FAILED:", engine, repr(exc))
            if engine == "scipy":
                traceback.print_exc(limit=2)