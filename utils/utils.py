import os
import re
import datetime
import arcpy


import os


def collect_rasters(
    iso3,
    ihme_root,
    year_from,
    quarter_from,
    year_to,
    quarter_to,
):
    """
    Collect IHME quarterly rasters for one ISO3 and a time range.

    Expected filename pattern:
        COD_2021q2_u16.tif
    """
    iso3 = iso3.upper()

    if quarter_from not in (1, 2, 3, 4):
        raise ValueError(f"quarter_from must be 1..4, got {quarter_from}")

    if quarter_to not in (1, 2, 3, 4):
        raise ValueError(f"quarter_to must be 1..4, got {quarter_to}")

    start_index = year_from * 4 + (quarter_from - 1)
    end_index = year_to * 4 + (quarter_to - 1)

    if start_index > end_index:
        raise ValueError(
            f"Start period {year_from}q{quarter_from} is after end period {year_to}q{quarter_to}"
        )

    matches = []

    for root, _, files in os.walk(ihme_root):
        for file_name in files:
            if not file_name.lower().endswith(".tif"):
                continue

            stem = os.path.splitext(file_name)[0]

            if not stem.endswith("_u16"):
                continue

            base = stem[:-4]  # remove "_u16"
            parts = base.split("_")

            if len(parts) != 2:
                continue

            file_iso3 = parts[0].upper()
            time_part = parts[1].lower()

            if file_iso3 != iso3:
                continue

            if len(time_part) != 6:
                continue

            year_part = time_part[:4]
            q_char = time_part[4]
            quarter_part = time_part[5]

            if q_char != "q":
                continue

            if not year_part.isdigit():
                continue

            if quarter_part not in ("1", "2", "3", "4"):
                continue

            file_year = int(year_part)
            file_quarter = int(quarter_part)

            file_index = file_year * 4 + (file_quarter - 1)

            if start_index <= file_index <= end_index:
                full_path = os.path.join(root, file_name)
                matches.append((file_year, file_quarter, full_path))

    matches.sort(key=lambda x: (x[0], x[1], x[2]))

    rasters = [item[2] for item in matches]

    if rasters:
        print(
            f"\n✅ Found {len(rasters)} raster(s) for {iso3} "
            f"in the specified time frame ({year_from}q{quarter_from} to {year_to}q{quarter_to}):"
        )
        for raster in rasters:
            print(raster)
    else:
        message = (
            f"\n⚠️ Found 0 rasters for {iso3} "
            f"in the specified time frame ({year_from}q{quarter_from} to {year_to}q{quarter_to})."
        )
        print(message)
        raise FileNotFoundError(message)

    return rasters


def ensure_file_gdb(fgdb_base, fgdb_name):
    """
    Create a file geodatabase and its parent folder if missing.

    Args:
        fgdb_base (str): Parent folder where the FGDB should live.
        fgdb_name (str): FGDB name, with or without '.gdb'.

    Returns:
        str: Full FGDB path.
    """
    # Create parent folder if missing
    os.makedirs(fgdb_base, exist_ok=True)

    if not fgdb_name.lower().endswith(".gdb"):
        fgdb_name = f"{fgdb_name}.gdb"

    fgdb_path = os.path.join(fgdb_base, fgdb_name)

    if arcpy.Exists(fgdb_path):
        print(f"\n⚠️ FGDB already exists: {fgdb_path}")
    else:
        arcpy.management.CreateFileGDB(
            out_folder_path=fgdb_base,
            out_name=fgdb_name
        )
        print(f"\n✅ Created FGDB: {fgdb_path}")

    return fgdb_path


def create_mosaic(fgdb_path):
    """
    Create a mosaic dataset inside the FGDB.

    Mosaic name = FGDB name without '.gdb'

    Args:
        fgdb_path (str): Full path to the FGDB.

    Returns:
        str: Full mosaic dataset path.
    """
    fgdb_basename = os.path.basename(fgdb_path)
    mosaic_name = os.path.splitext(fgdb_basename)[0]
    mosaic_path = os.path.join(fgdb_path, mosaic_name)

    if arcpy.Exists(mosaic_path):
        print(f"\n⚠️ Mosaic already exists: {mosaic_path}")
        return mosaic_path

    wgs84 = (
        'GEOGCS["GCS_WGS_1984",'
        'DATUM["D_WGS_1984",'
        'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],'
        'UNIT["Degree",0.0174532925199433]]'
    )

    arcpy.management.CreateMosaicDataset(
        in_workspace=fgdb_path,
        in_mosaicdataset_name=mosaic_name,
        coordinate_system=wgs84,
        num_bands=1,
        pixel_type="16_BIT_UNSIGNED",
        product_definition="NONE",
        product_band_definitions=None
    )

    print(f"\n✅ Created mosaic dataset: {mosaic_path}")
    return mosaic_path


def add_mosaic_fields(md):
    """
    Add the required custom fields to the mosaic dataset if they do not already exist.
    """
    existing_fields = {field.name for field in arcpy.ListFields(md)}

    required_fields = [
        ("projection_quarter", "DATE", None),
        ("variable", "TEXT", 50),
    ]

    for field_name, field_type, field_length in required_fields:
        if field_name in existing_fields:
            print(f"\n⚠️ Field already exists: {field_name}")
            continue

        if field_type == "TEXT":
            arcpy.management.AddField(
                in_table=md,
                field_name=field_name,
                field_type=field_type,
                field_length=field_length,
            )
        else:
            arcpy.management.AddField(
                in_table=md,
                field_name=field_name,
                field_type=field_type,
            )

        print(f"\n✅ Added field: {field_name}")


def populate_mosaic_fields(md):
    """
    Populate projection_quarter and variable from the mosaic item Name field.

    Rules:
    - projection_quarter: parsed from a token like 2025q1
    - variable: constant value for all rows
    """
    fields = ["Name", "projection_quarter", "variable"]

    with arcpy.da.UpdateCursor(md, fields) as cursor:
        for row in cursor:
            raster_name = row[0]

            # -------------------------
            # projection_quarter
            # -------------------------
            time_match = re.search(r"(20\d{2})q([1-4])", raster_name, flags=re.IGNORECASE)

            if time_match:
                year = int(time_match.group(1))
                quarter = int(time_match.group(2))

                quarter_month_lookup = {
                    1: 1,
                    2: 4,
                    3: 7,
                    4: 10,
                }

                row[1] = datetime.datetime(year, quarter_month_lookup[quarter], 1)
            else:
                row[1] = None
                print(f"\nWARNING: Could not parse quarter timestamp from Name: {raster_name}")

            # -------------------------
            # variable
            # -------------------------
            row[2] = "population_count"

            cursor.updateRow(row)

    print("\n✅ Finished populating mosaic fields.")

