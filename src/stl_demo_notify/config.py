from pathlib import Path

PARCEL_SHAPE_URL = "https://dynamic.stlouis-mo.gov/opendata/downloads/prcl_shape.zip"
LAND_RECORDS_URL = "https://dynamic.stlouis-mo.gov/opendata/downloads/par.zip"

# City of St. Louis Assessor Land Use vocabulary, id 24.
# https://www.stlouis-mo.gov/data/vocabularies/vocabulary.cfm?id=24
LANDUSE_VOCABULARY_URL = (
    "https://www.stlouis-mo.gov/customcf/endpoints/metadata/"
    "vocabulary-elements-download.cfm?id=24&format=csv"
)

# NAD83 Missouri East (meters). Geographic (lat/lon) coordinates do not
# support Euclidean distance, so buffer/distance math needs this instead.
CRS_EPSG = 26996
FEET_PER_METER = 3.280839895
DEFAULT_BUFFER_FEET = 500.0

# Match order against client APNs; address fallback after. HANDLE is the
# shapefile's join key to land records.
ID_COLUMNS = ["ASRPARCEL", "HANDLE", "PARCEL10", "PARCEL"]

OUTPUT_COLS = [
    "SITEADDR",
    "ZIP",
    "NUMBLDGS",
    "NUMUNITS",
    "LANDUSE1",
    "VACANTLAND",
    "ASMTIMPROV",
]

DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
PARCEL_CACHE_PATH = CACHE_DIR / "parcels.parquet"
LANDUSE_VOCABULARY_PATH = DATA_DIR / "landuse_vocabulary.csv"

# Scratch space for downloaded zips; not committed, not needed after
# PARCEL_CACHE_PATH is built.
DOWNLOAD_DIR = Path("stl_data")
