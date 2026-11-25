
from pathlib import Path
import os
import sys
import math

import django
import numpy as np

from scipy.sparse.csgraph import dijkstra
from scipy.sparse import load_npz


# ============================================================
# 1. PROJECT CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

GRAPH_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "optimized_road_graph.npz"
)

NODE_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "optimized_road_nodes.npz"
)

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

sys.path.insert(0, str(BASE_DIR))

django.setup()

from locations.models import Location


# ============================================================
# 2. DISTANCE CALCULATION
# ============================================================

def haversine(lat1, lon1, lat2, lon2):

    radius = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))

    return radius * 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1.0 - a),
    )


# ============================================================
# 3. LOAD DJANGO LOCATIONS
# ============================================================

def get_location(name, state):

    location = Location.objects.filter(
        name__iexact=name,
        state__iexact=state,
    ).first()

    if location is None:

        raise ValueError(
            f"Location not found: {name}, {state}"
        )

    return location


# ============================================================
# 4. FIND NEAREST ROAD NODE
# ============================================================

def find_nearest_node(
    target_lat,
    target_lon,
    node_ids,
    latitudes,
    longitudes,
):

    print(
        f"\nFinding nearest road node to "
        f"({target_lat:.6f}, {target_lon:.6f})..."
    )

    total_nodes = len(node_ids)

    chunk_size = 500000

    best_index = -1
    best_distance = float("inf")

    longitude_scale = math.cos(
        math.radians(target_lat)
    )

    for start in range(
        0,
        total_nodes,
        chunk_size,
    ):

        end = min(
            start + chunk_size,
            total_nodes,
        )

        lat_chunk = latitudes[start:end]
        lon_chunk = longitudes[start:end]

        lat_difference = lat_chunk - target_lat

        lon_difference = (
            lon_chunk - target_lon
        ) * longitude_scale

        squared_distances = (
            lat_difference * lat_difference
            + lon_difference * lon_difference
        )

        local_index = int(
            np.argmin(squared_distances)
        )

        local_distance = float(
            squared_distances[local_index]
        )

        if local_distance < best_distance:

            best_distance = local_distance

            best_index = start + local_index

    if best_index < 0:
        raise RuntimeError(
            "Could not find a nearby road node."
        )

    road_lat = float(latitudes[best_index])
    road_lon = float(longitudes[best_index])

    distance_km = haversine(
        target_lat,
        target_lon,
        road_lat,
        road_lon,
    )

    return {
        "index": best_index,
        "node_id": int(node_ids[best_index]),
        "latitude": road_lat,
        "longitude": road_lon,
        "distance_km": distance_km,
    }


# ============================================================
# 5. RECONSTRUCT SHORTEST PATH
# ============================================================

def reconstruct_path(
    source_index,
    target_index,
    predecessors,
    maximum_nodes,
):

    path = [target_index]

    current = target_index

    while current != source_index:

        previous = int(
            predecessors[current]
        )

        if previous < 0:
            raise RuntimeError(
                "Path reconstruction failed. "
                "The target may be unreachable."
            )

        path.append(previous)

        current = previous

        if len(path) > maximum_nodes:

            raise RuntimeError(
                "Path reconstruction exceeded "
                "the graph node count."
            )

    path.reverse()

    return path


# ============================================================
# 6. MAIN VALIDATION
# ============================================================

def main():

    print("\n===================================")
    print(" NE-LOGISMART ROAD NETWORK TEST")
    print("===================================")

    # --------------------------------------------------------
    # A. Check files
    # --------------------------------------------------------

    print("\n[1/6] Checking output files...")

    if not GRAPH_FILE.exists():
        raise FileNotFoundError(
            f"Graph file missing: {GRAPH_FILE}"
        )

    if not NODE_FILE.exists():
        raise FileNotFoundError(
            f"Node file missing: {NODE_FILE}"
        )

    print("Graph file found.")
    print("Node file found.")

    # --------------------------------------------------------
    # B. Load graph and coordinates
    # --------------------------------------------------------

    print("\n[2/6] Loading graph...")

    graph = load_npz(GRAPH_FILE).tocsr()

    node_data = np.load(
        NODE_FILE,
        allow_pickle=False,
    )

    node_ids = node_data["node_ids"]
    latitudes = node_data["latitudes"]
    longitudes = node_data["longitudes"]

    print("Graph loaded successfully.")

    print("Graph format:", graph.format)
    print("Graph dimensions:", graph.shape)
    print("Directed edges:", graph.nnz)

    # --------------------------------------------------------
    # C. Validate dimensions and weights
    # --------------------------------------------------------

    print("\n[3/6] Validating graph data...")

    if graph.shape[0] != len(node_ids):
        raise ValueError(
            "Graph node count does not match node file."
        )

    if len(node_ids) != len(latitudes):
        raise ValueError(
            "Node IDs and latitude counts differ."
        )

    if len(node_ids) != len(longitudes):
        raise ValueError(
            "Node IDs and longitude counts differ."
        )

    if graph.nnz == 0:
        raise ValueError(
            "The graph contains no edges."
        )

    if not np.all(np.isfinite(graph.data)):
        raise ValueError(
            "Graph contains invalid edge weights."
        )

    if np.any(graph.data <= 0):
        raise ValueError(
            "Graph contains zero or negative edge weights."
        )

    if not np.all(np.isfinite(latitudes)):
        raise ValueError(
            "Invalid latitude values found."
        )

    if not np.all(np.isfinite(longitudes)):
        raise ValueError(
            "Invalid longitude values found."
        )

    if (
        np.any(latitudes < -90)
        or np.any(latitudes > 90)
        or np.any(longitudes < -180)
        or np.any(longitudes > 180)
    ):
        raise ValueError(
            "Coordinates are outside valid geographical ranges."
        )

    if graph.indices.min() < 0:
        raise ValueError(
            "Graph contains a negative node index."
        )

    if graph.indices.max() >= graph.shape[0]:
        raise ValueError(
            "Graph contains an invalid node index."
        )

    print("Graph dimensions are consistent.")
    print("All edge weights are finite and positive.")
    print("All node coordinates are valid.")

    print(
        "Minimum edge distance:",
        round(float(graph.data.min()), 5),
        "km",
    )

    print(
        "Maximum edge distance:",
        round(float(graph.data.max()), 5),
        "km",
    )

    # --------------------------------------------------------
    # D. Find Guwahati and Shillong
    # --------------------------------------------------------

    print("\n[4/6] Loading test locations from Django...")

    source_location = get_location(
        "Guwahati",
        "Assam",
    )

    destination_location = get_location(
        "Shillong",
        "Meghālaya",
    )

    print(
        "Source:",
        source_location.name,
        source_location.state,
    )

    print(
        "Destination:",
        destination_location.name,
        destination_location.state,
    )

    # --------------------------------------------------------
    # E. Snap cities to nearest road nodes
    # --------------------------------------------------------

    source_node = find_nearest_node(
        source_location.latitude,
        source_location.longitude,
        node_ids,
        latitudes,
        longitudes,
    )

    destination_node = find_nearest_node(
        destination_location.latitude,
        destination_location.longitude,
        node_ids,
        latitudes,
        longitudes,
    )

    print("\nNearest road nodes:")

    print(
        "Guwahati OSM node:",
        source_node["node_id"],
    )

    print(
        "Guwahati snapping distance:",
        round(source_node["distance_km"], 3),
        "km",
    )

    print(
        "Shillong OSM node:",
        destination_node["node_id"],
    )

    print(
        "Shillong snapping distance:",
        round(destination_node["distance_km"], 3),
        "km",
    )

    if source_node["index"] == destination_node["index"]:
        raise RuntimeError(
            "Both cities snapped to the same road node. "
            "Cannot perform this route test."
        )

    # --------------------------------------------------------
    # F. Calculate shortest road path
    # --------------------------------------------------------

    print("\n[5/6] Calculating shortest road path...")

    print(
        "Starting Dijkstra's algorithm. "
        "This may take some time."
    )

    distances, predecessors = dijkstra(
        graph,
        directed=True,
        indices=source_node["index"],
        return_predecessors=True,
    )

    target_index = destination_node["index"]

    route_distance = float(
        distances[target_index]
    )

    if not math.isfinite(route_distance):

        raise RuntimeError(
            "No directed road path was found from "
            "Guwahati to Shillong."
        )

    path = reconstruct_path(
        source_node["index"],
        target_index,
        predecessors,
        graph.shape[0],
    )

    route_osm_ids = [
        int(node_ids[index])
        for index in path
    ]

    print("\n[6/6] Route validation completed.")

    print("\n========== ROUTE RESULT ==========")

    print("From: Guwahati, Assam")
    print("To: Shillong, Meghalaya")

    print(
        "Shortest road distance:",
        round(route_distance, 2),
        "km",
    )

    print(
        "Road network nodes in path:",
        len(path),
    )

    print(
        "Source road node:",
        route_osm_ids[0],
    )

    print(
        "Destination road node:",
        route_osm_ids[-1],
    )

    print("\nFirst five OSM node IDs:")

    print(route_osm_ids[:5])

    print("\nLast five OSM node IDs:")

    print(route_osm_ids[-5:])

    print("\n===================================")
    print(" ROAD NETWORK VALIDATION PASSED")
    print("===================================")


if __name__ == "__main__":
    main()
