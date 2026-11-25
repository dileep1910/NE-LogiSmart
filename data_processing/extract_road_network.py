
import math
import pickle
from pathlib import Path

import osmium
import networkx as nx


# -----------------------------------------
# PROJECT PATHS
# -----------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

PBF_FILE = (
    BASE_DIR
    / "data"
    / "raw"
    / "north-eastern-zone-261102.osm.pbf"
)

OUTPUT_DIR = BASE_DIR / "data" / "processed"

GRAPH_FILE = OUTPUT_DIR / "northeast_road_network.pkl"


# -----------------------------------------
# GEOGRAPHICAL BOUNDING BOX
# Approximate Northeast India coverage
# -----------------------------------------

MIN_LAT = 21.5
MAX_LAT = 29.5

MIN_LON = 88.0
MAX_LON = 97.5


# -----------------------------------------
# ROAD TYPES
# -----------------------------------------

ALLOWED_ROADS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
}


# -----------------------------------------
# HAVERSINE DISTANCE
# Calculates approximate distance
# between two geographical coordinates.
# -----------------------------------------

def calculate_distance(lat1, lon1, lat2, lon2):

    earth_radius_km = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_km * c


# -----------------------------------------
# CHECK COORDINATES
# -----------------------------------------

def is_inside_region(lat, lon):

    return (
        MIN_LAT <= lat <= MAX_LAT
        and MIN_LON <= lon <= MAX_LON
    )


# -----------------------------------------
# OSM ROAD EXTRACTION HANDLER
# -----------------------------------------

class RoadNetworkHandler(osmium.SimpleHandler):

    def __init__(self):

        super().__init__()

        self.graph = nx.MultiDiGraph()

        self.total_ways = 0
        self.accepted_ways = 0
        self.total_segments = 0

    def way(self, way):

        self.total_ways += 1

        highway_type = way.tags.get("highway")

        if highway_type not in ALLOWED_ROADS:
            return

        self.accepted_ways += 1

        previous_node = None

        for node in way.nodes:

            if not node.location.valid():
                previous_node = None
                continue

            lon = node.location.lon
            lat = node.location.lat

            if not is_inside_region(lat, lon):
                previous_node = None
                continue

            node_id = int(node.ref)

            self.graph.add_node(
                node_id,
                latitude=lat,
                longitude=lon,
            )

            if previous_node is not None:

                previous_id, previous_lat, previous_lon = previous_node

                distance = calculate_distance(
                    previous_lat,
                    previous_lon,
                    lat,
                    lon,
                )

                if distance > 0:

                    self.graph.add_edge(
                        previous_id,
                        node_id,
                        distance_km=distance,
                        highway=highway_type,
                        oneway=way.tags.get("oneway", "no"),
                    )

                    # Add reverse direction unless
                    # the road is explicitly one-way.

                    if way.tags.get("oneway") not in {"yes", "1", "true"}:

                        self.graph.add_edge(
                            node_id,
                            previous_id,
                            distance_km=distance,
                            highway=highway_type,
                            oneway=way.tags.get("oneway", "no"),
                        )

                    self.total_segments += 1

            previous_node = (node_id, lat, lon)


# -----------------------------------------
# MAIN FUNCTION
# -----------------------------------------

def main():

    if not PBF_FILE.exists():

        print("ERROR: OpenStreetMap PBF file not found.")
        print(PBF_FILE)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("NE-LogiSmart Road Network Extraction")
    print("=" * 55)

    print("Input file:", PBF_FILE.name)
    print("Processing OpenStreetMap roads...")
    print("This may take several minutes.")

    handler = RoadNetworkHandler()

    handler.apply_file(
        str(PBF_FILE),
        locations=True,
        idx="flex_mem",
    )

    print("\nExtraction completed.")

    print("Total OSM ways processed:", handler.total_ways)
    print("Accepted road ways:", handler.accepted_ways)
    print("Road segments created:", handler.total_segments)

    print("Graph nodes:", handler.graph.number_of_nodes())
    print("Graph edges:", handler.graph.number_of_edges())

    print("\nSaving road network...")

    with open(GRAPH_FILE, "wb") as file:

        pickle.dump(
            handler.graph,
            file,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    print("Road network saved successfully.")
    print("Output:", GRAPH_FILE)

    print("=" * 55)
    print("ROAD NETWORK EXTRACTION FINISHED")
    print("=" * 55)


if __name__ == "__main__":
    main()
