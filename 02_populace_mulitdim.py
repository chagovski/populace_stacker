import os
import arcpy

from utils.utils import collect_rasters, ensure_file_gdb, create_mosaic, add_mosaic_fields, populate_mosaic_fields


# ------------------------------------------------------
# REQUIRED INPUTS
# ------------------------------------------------------

country_iso = "COD"

year_from = 2020
quarter_from = 2
year_to = 2020
quarter_to = 3

# ------------------------------------------------------
input_root = rf"I:\02_datapool\POPULATION\populace\{country_iso.upper()}"
processed_root = os.path.join(input_root, "02_processed")

rasters = collect_rasters(
    iso3=country_iso,
    ihme_root=processed_root,
    year_from=year_from,
    quarter_from=quarter_from,
    year_to=year_to,
    quarter_to=quarter_to,
)

# ------------------------------------------------------
# File Geodatabase
# ------------------------------------------------------

fgdb_root = os.path.join(processed_root, "_mosaics")

time_tag = f"{year_from}q{quarter_from}_{year_to}q{quarter_to}"
fgdb_name = f"{country_iso.upper()}_Populace_{time_tag}.gdb"

fgdb = ensure_file_gdb(fgdb_base=fgdb_root, fgdb_name=fgdb_name)

# ------------------------------------------------------
# Mosaic Dataset
# ------------------------------------------------------

md = create_mosaic(fgdb_path=fgdb)

# ------------------------------------------------------
# Add Dimension Fields
# ------------------------------------------------------

add_mosaic_fields(md)

# ------------------------------------------------------
# Add Rasters
# ------------------------------------------------------

for raster in rasters:
    arcpy.management.AddRastersToMosaicDataset(
        in_mosaic_dataset=md,
        raster_type="Raster Dataset",
        input_path=raster,
        duplicate_items_action="EXCLUDE_DUPLICATES"
    )

# ------------------------------------------------------
# Populate Dimension Fields
# ------------------------------------------------------

populate_mosaic_fields(md)

# ------------------------------------------------------
# Build Multidimensional Info
# ------------------------------------------------------

arcpy.management.BuildMultidimensionalInfo(
    in_mosaic_dataset=md,
    variable_field="variable",
    dimension_fields=(
        "projection_quarter "
        "'quarter of the population projection' "
        "'quarter'"
    ),
    variable_desc_units=(
        "population_count "
        "'Populace population count for each projection quarter' "
        "'people per pixel'"
    )
)

# ------------------------------------------------------
# Export CRF
# ------------------------------------------------------

crf_root = os.path.join(processed_root, "_outputs")
os.makedirs(crf_root, exist_ok=True)

crf_name = f"{country_iso.upper()}_Populace_{time_tag}.crf"
out_crf = os.path.join(crf_root, crf_name)

if arcpy.Exists(out_crf):
    arcpy.management.Delete(out_crf)

with arcpy.EnvManager(
    compression="'LERC' 0",
    tileSize="512 512",
    parallelProcessingFactor="50%"
):
    arcpy.management.CopyRaster(
        in_raster=md,
        out_rasterdataset=out_crf,
        format="CRF",
        process_as_multidimensional="ALL_SLICES",
        build_multidimensional_transpose="TRANSPOSE"
    )

print("\n✅ Exported CRF.")

# ------------------------------------------------------
# Statistics
# ------------------------------------------------------

arcpy.management.CalculateStatistics(
    in_raster_dataset=out_crf,
    x_skip_factor=1,
    y_skip_factor=1,
    ignore_values="0",
    skip_existing="OVERWRITE"
)

print("\n✅ Calculated statistics.")

# ------------------------------------------------------
# Metadata
# ------------------------------------------------------

from arcpy import metadata as md

crf_md = md.Metadata(out_crf)

crf_md.title = f"{country_iso.upper()} Populace population data"
crf_md.tags = f"Populace, IHME, population, multidimensional, CRF, {country_iso.upper()}"
crf_md.summary = "Multidimensional Populace population count raster for selected projection timestamps."
crf_md.description = (
    "Populace is a global map of where people live, estimating the number of residents in every 40 m × 40 m grid cell of the world’s land surface. "
    "It is updated quarterly, with estimates from the first quarter of 2020 onward. "
    "Populace is produced by the Institute for Health Metrics and Evaluation (IHME) in collaboration with Planet and Microsoft’s AI for Good Lab." 
)
crf_md.credits = "Populace; IHME; Planet; Microsoft AI for Good"
crf_md.accessConstraints = "Review methodology and limitations before analytical use."

crf_md.save()

print("\n✅ Updated CRF Metadata.")