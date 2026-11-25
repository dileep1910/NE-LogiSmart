
import csv
import osmium

from pathlib import Path


# Project directories
BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    BASE_DIR
    / "data"
    / "raw"
    / "north-eastern-zone-261102.osm.pbf"
)

OUTPUT_DIR = BASE_DIR / "data" / "processed"

OUTPUT_FILE = OUTPUT_DIR / "northeast_locations.csv"


class LocationExtractor(osmium.SimpleHandler):

    def __init__(self):
        super().__init__()
        self.locations = []

    def node(self, node):

        if not node.location.valid():
            return

        tags = node.tags

        name = tags.get("name")

        if not name:
            return

        place_type = tags.get("place")
        railway_type = tags.get("railway")

        # Identify cities, towns and villages
        if place_type in ("city", "town", "village"):

            category = place_type

        # Identify railway stations
        elif railway_type in ("station", "halt"):

            category = "railway_station"

        else:
            return

        self.locations.append({
            "name": name,
            "category": category,
            "latitude": node.location.lat,
            "longitude": node.location.lon,
        })


def main():

    if not INPUT_FILE.exists():
        print("ERROR: OpenStreetMap PBF file not found.")
        print(f"Expected location: {INPUT_FILE}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting OpenStreetMap extraction...")
    print("This may take some time.")

    handler = LocationExtractor()

    handler.apply_file(
        str(INPUT_FILE),
        locations=False
    )

    # Remove duplicate records
    unique_locations = {}

    for location in handler.locations:

        key = (
            location["name"],
            location["category"],
            location["latitude"],
            location["longitude"],
        )

        unique_locations[key] = location

    locations = list(unique_locations.values())

    # Export to CSV
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as csvfile:

        fieldnames = [
            "name",
            "category",
            "latitude",
            "longitude",
        ]

        writer = csv.DictWriter(
            csvfile,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(locations)

    print("\nExtraction completed!")
    print(f"Total locations: {len(locations)}")
    print(f"Output file: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
