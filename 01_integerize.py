from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
from osgeo import gdal


# -------------------------------------------------------------------
# Country ISO3 code: THE ONLY REQUIRED INPUT 
# -------------------------------------------------------------------
country_iso = "COD"

# -------------------------------------------------------------------

INPUT_FOLDER = Path(rf"I:\02_datapool\POPULATION\IHME\{country_iso.upper()}\01_raw")
OUTPUT_FOLDER = INPUT_FOLDER.parent / "02_processed"

# Always recursive
RECURSIVE = True

# Overwrite existing outputs?
OVERWRITE = True

# Output nodata for UInt16
U16_NODATA = 0
U16_VALID_MIN = 1      # reserve 0 for nodata
U16_VALID_MAX = 65535


def build_file_list(input_folder: Path, output_folder: Path) -> list[Path]:
    files = []
    for p in input_folder.rglob("*.tif"):
        if not p.is_file():
            continue

        # Skip anything already in the output tree
        try:
            p.relative_to(output_folder)
            continue
        except ValueError:
            pass

        # Skip already-converted files
        if p.stem.endswith("_u16"):
            continue

        files.append(p)

    return sorted(files)


def get_output_path(src_path: Path) -> Path:
    rel_path = src_path.relative_to(INPUT_FOLDER)
    out_rel = rel_path.with_name(f"{rel_path.stem}_u16{rel_path.suffix}")
    return OUTPUT_FOLDER / out_rel


def convert_one_raster(src_path: Path, overwrite: bool = False) -> Path:
    out_path = get_output_path(src_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not overwrite:
        print(f"Skipping existing output: {out_path}")
        return out_path

    src_ds = gdal.Open(str(src_path), gdal.GA_ReadOnly)
    if src_ds is None:
        raise RuntimeError(f"Could not open input raster: {src_path}")

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GTiff driver not available.")

    creation_options = [
        "TILED=YES",
        "COMPRESS=ZSTD",
        "PREDICTOR=2",
        "BIGTIFF=IF_SAFER",
        "NUM_THREADS=ALL_CPUS",
    ]

    if out_path.exists() and overwrite:
        driver.Delete(str(out_path))

    out_ds = driver.Create(
        str(out_path),
        src_ds.RasterXSize,
        src_ds.RasterYSize,
        src_ds.RasterCount,
        gdal.GDT_UInt16,
        options=creation_options,
    )
    if out_ds is None:
        raise RuntimeError(f"Could not create output raster: {out_path}")

    try:
        out_ds.SetGeoTransform(src_ds.GetGeoTransform())
        out_ds.SetProjection(src_ds.GetProjection())
        out_ds.SetMetadata(src_ds.GetMetadata())

        for band_idx in range(1, src_ds.RasterCount + 1):
            src_band = src_ds.GetRasterBand(band_idx)
            out_band = out_ds.GetRasterBand(band_idx)

            out_band.SetNoDataValue(U16_NODATA)
            out_band.SetColorInterpretation(src_band.GetColorInterpretation())
            out_band.SetMetadata(src_band.GetMetadata())
            out_band.SetDescription(src_band.GetDescription())

            scale = src_band.GetScale()
            offset = src_band.GetOffset()
            unit_type = src_band.GetUnitType()

            if scale is not None:
                out_band.SetScale(scale)
            if offset is not None:
                out_band.SetOffset(offset)
            if unit_type:
                out_band.SetUnitType(unit_type)

            src_nodata = src_band.GetNoDataValue()

            block_x, block_y = src_band.GetBlockSize()
            if not block_x or block_x <= 0:
                block_x = 512
            if not block_y or block_y <= 0:
                block_y = 512

            xsize = src_band.XSize
            ysize = src_band.YSize

            nodata_count = 0
            clipped_low_count = 0
            clipped_high_count = 0

            for yoff in range(0, ysize, block_y):
                rows = min(block_y, ysize - yoff)

                for xoff in range(0, xsize, block_x):
                    cols = min(block_x, xsize - xoff)

                    arr = src_band.ReadAsArray(xoff, yoff, cols, rows)
                    if arr is None:
                        raise RuntimeError(
                            f"Failed reading block x={xoff}, y={yoff}, cols={cols}, rows={rows}"
                        )

                    arr = arr.astype(np.float64, copy=False)

                    # Source nodata / NaN / inf -> output nodata
                    mask = ~np.isfinite(arr)

                    if src_nodata is not None:
                        if np.isnan(src_nodata):
                            mask |= np.isnan(arr)
                        else:
                            mask |= (arr == src_nodata)

                    nodata_count += int(mask.sum())

                    # Round to nearest whole number
                    rounded = np.rint(arr)

                    # Reserve 0 for nodata, so valid values become 1..65535
                    low_mask = (~mask) & (rounded < U16_VALID_MIN)
                    high_mask = (~mask) & (rounded > U16_VALID_MAX)

                    clipped_low_count += int(low_mask.sum())
                    clipped_high_count += int(high_mask.sum())

                    rounded = np.clip(rounded, U16_VALID_MIN, U16_VALID_MAX)

                    out_arr = rounded.astype(np.uint16)
                    out_arr[mask] = U16_NODATA

                    out_band.WriteArray(out_arr, xoff=xoff, yoff=yoff)

            out_band.FlushCache()

            print(
                f"  Band {band_idx}: nodata={nodata_count}, "
                f"clipped_low={clipped_low_count}, clipped_high={clipped_high_count}"
            )

        out_ds.FlushCache()
        print(f"Written: {out_path}")
        return out_path

    except Exception:
        out_ds = None
        src_ds = None
        if out_path.exists():
            try:
                driver.Delete(str(out_path))
            except Exception:
                pass
        raise

    finally:
        out_ds = None
        src_ds = None


def main() -> int:
    if not INPUT_FOLDER.exists() or not INPUT_FOLDER.is_dir():
        print(f"Input folder does not exist or is not a directory: {INPUT_FOLDER}", file=sys.stderr)
        return 1

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    gdal.UseExceptions()

    files = build_file_list(INPUT_FOLDER, OUTPUT_FOLDER)
    if not files:
        print(f"No input .tif files found in: {INPUT_FOLDER}")
        return 0

    print(f"Found {len(files)} raster(s) in: {INPUT_FOLDER}")
    print(f"Output root: {OUTPUT_FOLDER}")

    failed = 0
    for i, src_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] Processing: {src_path}")
        try:
            convert_one_raster(src_path, overwrite=OVERWRITE)
        except Exception as exc:
            failed += 1
            print(f"  ERROR: {exc}", file=sys.stderr)

    if failed:
        print(f"Done with {failed} failure(s).", file=sys.stderr)
        return 2

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
