
import json
import requests
import pandas as pd

from pathlib import Path
from shapely.geometry import shape, Point


# --------------------------------------------------
# 1. Project paths
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    BASE_DIR / "data" / "processed" / "northeast_locations.csv"
)

BOUNDARY_DIR = BASE_DIR / "data" / "boundaries"

BOUNDARY_FILE = (
    BOUNDARY_DIR / "india_adm1_boundaries.geojson"
)

OUTPUT_FILE = (
    BASE_DIR / "data" / "processed"
    / "northeast_locations_with_states.csv"
)


# --------------------------------------------------
# 2. geoBoundaries API
# --------------------------------------------------

API_URL = (
    "https://www.geoboundaries.org/api/current/"
    "gbOpen/IND/ADM1/"
)


def download_boundaries():

    if BOUNDARY_FILE.exists():
        print("Boundary file already exists. Using saved file.")
        return

    print("Fetching India state-boundary information...")

    response = requests.get(API_URL, timeout=60)
    response.raise_for_status()

    metadata = response.json()

    download_url = (
        metadata.get("simplifiedGeometryGeoJSON")
        or metadata.get("gjDownloadURL")
    )

    if not download_url:
        raise ValueError("GeoJSON download URL not found in API response.")

    print("Downloading state boundaries...")

    boundary_response = requests.get(
        download_url,
        timeout=120
    )

    boundary_response.raise_for_status()

    BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)

    with open(BOUNDARY_FILE, "wb") as file:
        file.write(boundary_response.content)

    print("Boundary download completed.")


# --------------------------------------------------
# 3. Load state polygons
# --------------------------------------------------

def load_state_boundaries():

    with open(
        BOUNDARY_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        geojson_data = json.load(file)

    state_boundaries = []

    for feature in geojson_data["features"]:

        properties = feature.get("properties", {})

        state_name = (
            properties.get("shapeName")
            or properties.get("NAME_1")
            or properties.get("name")
        )

        if not state_name:
            raise ValueError(
                "Could not identify a state name in boundary properties."
            )

        polygon = shape(feature["geometry"])

        if not polygon.is_valid:
            polygon = polygon.buffer(0)

        state_boundaries.append({
            "name": state_name,
            "geometry": polygon,
        })

    print("State boundaries loaded:", len(state_boundaries))

    return state_boundaries


# --------------------------------------------------
# 4. Find the state for each location
# --------------------------------------------------

def find_state(latitude, longitude, boundaries):

    point = Point(longitude, latitude)

    for boundary in boundaries:

        if boundary["geometry"].covers(point):
            return boundary["name"]

    return ""


# --------------------------------------------------
# 5. Main processing function
# --------------------------------------------------

def main():

    if not INPUT_FILE.exists():
        print("ERROR: Original location CSV not found.")
        print(INPUT_FILE)
        return

    print("Loading original location dataset...")

    df = pd.read_csv(INPUT_FILE)

    required_columns = {
        "name",
        "category",
        "latitude",
        "longitude",
    }

    if not required_columns.issubset(df.columns):
        raise ValueError("The input CSV is missing required columns.")

    download_boundaries()

    boundaries = load_state_boundaries()

    print("\nMatching locations to states...")
    print("Please wait...")

    df["state"] = df.apply(
        lambda row: find_state(
            row["latitude"],
            row["longitude"],
            boundaries
        ),
        axis=1
    )

    # Save enriched dataset without changing original CSV
    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8"
    )

    matched = df["state"].ne("").sum()
    unmatched = df["state"].eq("").sum()

    print("\n--------------------------------")
    print("STATE ENRICHMENT COMPLETED")
    print("--------------------------------")

    print("Total locations:", len(df))
    print("Matched locations:", matched)
    print("Unmatched locations:", unmatched)

    print("\nLocations by state:")

    print(
        df[df["state"] != ""]["state"]
        .value_counts()
        .to_string()
    )

    print("\nUnmatched location examples:")

    print(
        df[df["state"] == ""]["name"]
        .head(15)
        .to_string(index=False)
    )

    print("\nOutput file:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
