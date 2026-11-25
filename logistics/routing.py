from pathlib import Path
import math
import threading

import numpy as np

from scipy.sparse import load_npz
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


# ============================================================
# 1. FILE PATHS
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


# ============================================================
# 2. ROUTING ENGINE
# ============================================================

class RoadRoutingEngine:

    def __init__(self):

        self.graph = None

        self.node_ids = None
        self.latitudes = None
        self.longitudes = None

        self.spatial_tree = None

        self.loaded = False

        # Protect graph operations while weights are temporarily changed.
        self.graph_lock = threading.Lock()

    # --------------------------------------------------------
    # LOAD GRAPH AND BUILD SPATIAL INDEX
    # --------------------------------------------------------

    def load(self):

        if self.loaded:
            return

        if not GRAPH_FILE.exists():
            raise FileNotFoundError(
                f"Road graph not found: {GRAPH_FILE}"
            )

        if not NODE_FILE.exists():
            raise FileNotFoundError(
                f"Road node file not found: {NODE_FILE}"
            )

        print("Loading road network...")

        self.graph = load_npz(
            GRAPH_FILE
        ).tocsr()

        # Required for binary edge-position searching.
        self.graph.sort_indices()

        node_data = np.load(
            NODE_FILE,
            allow_pickle=False,
        )

        self.node_ids = node_data["node_ids"]
        self.latitudes = node_data["latitudes"]
        self.longitudes = node_data["longitudes"]

        if self.graph.shape[0] != len(self.node_ids):
            raise ValueError(
                "Road graph and node data dimensions do not match."
            )

        print("Building geographical search index...")

        lat_radians = np.radians(
            self.latitudes.astype(np.float64)
        )

        lon_radians = np.radians(
            self.longitudes.astype(np.float64)
        )

        cos_lat = np.cos(lat_radians)

        coordinates = np.column_stack((
            cos_lat * np.cos(lon_radians),
            cos_lat * np.sin(lon_radians),
            np.sin(lat_radians),
        ))

        self.spatial_tree = cKDTree(
            coordinates,
            copy_data=False,
        )

        self.loaded = True

        print("Road network loaded successfully.")
        print("Nodes:", len(self.node_ids))
        print("Directed edges:", self.graph.nnz)

    # --------------------------------------------------------
    # HAVERSINE DISTANCE
    # --------------------------------------------------------

    @staticmethod
    def haversine(
        lat1,
        lon1,
        lat2,
        lon2,
    ):

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

    # --------------------------------------------------------
    # FIND NEAREST ROAD NODE
    # --------------------------------------------------------

    def find_nearest_node(
        self,
        latitude,
        longitude,
    ):

        lat_radians = math.radians(latitude)
        lon_radians = math.radians(longitude)

        cos_lat = math.cos(lat_radians)

        query_point = [
            cos_lat * math.cos(lon_radians),
            cos_lat * math.sin(lon_radians),
            math.sin(lat_radians),
        ]

        _, index = self.spatial_tree.query(
            query_point,
            k=1,
        )

        index = int(index)

        road_lat = float(
            self.latitudes[index]
        )

        road_lon = float(
            self.longitudes[index]
        )

        snapping_distance = self.haversine(
            latitude,
            longitude,
            road_lat,
            road_lon,
        )

        return {
            "index": index,
            "node_id": int(self.node_ids[index]),
            "latitude": road_lat,
            "longitude": road_lon,
            "snapping_distance_km": snapping_distance,
        }

    # --------------------------------------------------------
    # RECONSTRUCT PATH
    # --------------------------------------------------------

    @staticmethod
    def reconstruct_path(
        source_index,
        destination_index,
        predecessors,
        maximum_nodes,
    ):

        path = [destination_index]

        current = destination_index

        while current != source_index:

            previous = int(
                predecessors[current]
            )

            if previous < 0:
                raise ValueError(
                    "A road path could not be reconstructed."
                )

            path.append(previous)

            current = previous

            if len(path) > maximum_nodes:
                raise RuntimeError(
                    "Path reconstruction exceeded graph size."
                )

        path.reverse()

        return path

    # --------------------------------------------------------
    # FIND CSR EDGE POSITION
    # --------------------------------------------------------

    def find_edge_position(
        self,
        source_index,
        destination_index,
    ):

        row_start = self.graph.indptr[source_index]
        row_end = self.graph.indptr[source_index + 1]

        row_indices = self.graph.indices[
            row_start:row_end
        ]

        position = np.searchsorted(
            row_indices,
            destination_index,
        )

        if position >= len(row_indices):
            return None

        if row_indices[position] != destination_index:
            return None

        return int(row_start + position)

    # --------------------------------------------------------
    # CALCULATE ACTUAL PATH DISTANCE
    # --------------------------------------------------------

    def calculate_path_distance(self, path):

        total_distance = 0.0

        # Prevent another thread from temporarily changing edge
        # weights while the actual distance is being calculated.
        with self.graph_lock:

            for i in range(len(path) - 1):

                edge_position = self.find_edge_position(
                    path[i],
                    path[i + 1],
                )

                if edge_position is None:
                    raise ValueError(
                        "A path contains an edge that does not "
                        "exist in the road graph."
                    )

                total_distance += float(
                    self.graph.data[edge_position]
                )

        return total_distance

    # --------------------------------------------------------
    # FIND ALTERNATIVE PATH WITH TEMPORARY PENALTIES
    # --------------------------------------------------------

    def find_alternative_path_with_penalty(
        self,
        source_index,
        destination_index,
        penalty_edges,
        penalty_multiplier=5.0,
    ):

        self.load()

        if penalty_multiplier <= 1.0:
            raise ValueError(
                "Penalty multiplier must be greater than 1."
            )

        if source_index == destination_index:
            raise ValueError(
                "Source and destination cannot be the same node."
            )

        # Store positions and original weights so they can
        # be restored after the search.
        saved_weights = {}

        with self.graph_lock:

            try:

                # Locate and temporarily penalize selected edges.
                for edge in penalty_edges:

                    edge_source = int(edge[0])
                    edge_destination = int(edge[1])

                    edge_position = self.find_edge_position(
                        edge_source,
                        edge_destination,
                    )

                    if edge_position is None:
                        continue

                    # Avoid applying the same edge penalty twice.
                    if edge_position in saved_weights:
                        continue

                    original_weight = float(
                        self.graph.data[edge_position]
                    )

                    saved_weights[edge_position] = (
                        original_weight
                    )

                    self.graph.data[edge_position] = (
                        original_weight * penalty_multiplier
                    )

                print(
                    "Running alternative route search "
                    "with temporary penalties..."
                )

                # Run Dijkstra against the temporarily
                # modified graph.
                distances, predecessors = dijkstra(
                    self.graph,
                    directed=True,
                    indices=source_index,
                    return_predecessors=True,
                )

                alternative_distance = float(
                    distances[destination_index]
                )

                if not math.isfinite(alternative_distance):
                    raise ValueError(
                        "No alternative route could be found."
                    )

                alternative_path = self.reconstruct_path(
                    source_index,
                    destination_index,
                    predecessors,
                    self.graph.shape[0],
                )

            finally:

                # Always restore original graph weights,
                # even if Dijkstra or path reconstruction fails.
                for edge_position, original_weight in (
                    saved_weights.items()
                ):
                    self.graph.data[edge_position] = (
                        original_weight
                    )

        # Calculate the true distance after the original
        # graph weights have been restored.
        actual_distance = self.calculate_path_distance(
            alternative_path
        )

        return {
            "path_indices": alternative_path,
            "node_count": len(alternative_path),
            "penalized_distance_km": alternative_distance,
            "actual_distance_km": actual_distance,
        }

    # --------------------------------------------------------
    # FIND SHORTEST ROUTE
    # --------------------------------------------------------

    def find_route(
        self,
        source_latitude,
        source_longitude,
        destination_latitude,
        destination_longitude,
    ):

        self.load()

        source_node = self.find_nearest_node(
            source_latitude,
            source_longitude,
        )

        destination_node = self.find_nearest_node(
            destination_latitude,
            destination_longitude,
        )

        if (
            source_node["index"]
            == destination_node["index"]
        ):
            raise ValueError(
                "Source and destination are too close "
                "to use as separate road-network nodes."
            )

        print("Calculating shortest road route...")

        with self.graph_lock:

            distances, predecessors = dijkstra(
                self.graph,
                directed=True,
                indices=source_node["index"],
                return_predecessors=True,
            )

        destination_index = destination_node["index"]

        route_distance = float(
            distances[destination_index]
        )

        if not math.isfinite(route_distance):
            raise ValueError(
                "No road route is available between "
                "these two locations in the extracted network."
            )

        path = self.reconstruct_path(
            source_node["index"],
            destination_index,
            predecessors,
            self.graph.shape[0],
        )

        route_coordinates = [
            {
                "latitude": float(self.latitudes[index]),
                "longitude": float(self.longitudes[index]),
            }
            for index in path
        ]

        return {
            "source_node": source_node,
            "destination_node": destination_node,
            "distance_km": route_distance,
            "node_count": len(path),
            "coordinates": route_coordinates,
            "path_indices": path,
        }


# ============================================================
# 3. SINGLE SHARED ENGINE INSTANCE
# ============================================================

_engine = None
_engine_lock = threading.Lock()


def get_routing_engine():

    global _engine

    if _engine is None:

        with _engine_lock:

            if _engine is None:

                _engine = RoadRoutingEngine()

    _engine.load()

    return _engine


# ============================================================
# 4. PUBLIC ROUTING FUNCTION
# ============================================================

def calculate_road_route(
    source,
    destination,
):

    engine = get_routing_engine()

    return engine.find_route(
        source.latitude,
        source.longitude,
        destination.latitude,
        destination.longitude,
    )