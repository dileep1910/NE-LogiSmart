
from pathlib import Path
import argparse
import gc
import math
import shutil
import sqlite3

import numpy as np
import osmium

from scipy.sparse import csr_matrix, save_npz


# ============================================================
# 1. PATHS AND CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

PBF_FILE = (
    BASE_DIR
    / "data"
    / "raw"
    / "north-eastern-zone-261102.osm.pbf"
)

OUTPUT_DIR = BASE_DIR / "data" / "processed"

# Disk-backed intermediate database.
DATABASE_FILE = OUTPUT_DIR / "road_network_working.sqlite3"

# Final output files.
GRAPH_FILE = OUTPUT_DIR / "optimized_road_graph.npz"
NODE_FILE = OUTPUT_DIR / "optimized_road_nodes.npz"

# Temporary memory-mapped arrays used during graph construction.
WORK_DIR = OUTPUT_DIR / "road_network_work"

MIN_LAT = 21.5
MAX_LAT = 29.5
MIN_LON = 88.0
MAX_LON = 97.5

# Flush extraction records to SQLite in batches.
BATCH_SIZE = 25000

# Number of database records fetched per processing batch.
READ_BATCH_SIZE = 100000

ROAD_TYPES = {
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


# ============================================================
# 2. DISTANCE CALCULATION
# ============================================================

def haversine(lat1, lon1, lat2, lon2):
    """Calculate the distance between two coordinates in km."""

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
# 3. DATABASE SETUP
# ============================================================

def configure_database(connection):
    """Configure SQLite for large disk-backed processing."""

    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA cache_size=-262144")
    connection.execute("PRAGMA temp_store=FILE")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id INTEGER PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            matrix_index INTEGER
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            distance REAL NOT NULL,
            PRIMARY KEY (from_id, to_id)
        ) WITHOUT ROWID
    """)

    connection.commit()


def set_metadata(connection, key, value):
    connection.execute(
        """
        INSERT INTO metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def get_metadata(connection, key):
    result = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()

    return result[0] if result else None


def extraction_is_complete():
    """Check whether the database contains a completed extraction."""

    if not DATABASE_FILE.exists():
        return False

    connection = sqlite3.connect(DATABASE_FILE)

    try:
        result = get_metadata(
            connection,
            "extraction_complete",
        )
        return result == "1"

    except sqlite3.DatabaseError:
        return False

    finally:
        connection.close()


# ============================================================
# 4. ROAD EXTRACTION HANDLER
# ============================================================

class RoadNetworkHandler(osmium.SimpleHandler):

    def __init__(self, connection):

        super().__init__()

        self.connection = connection

        self.total_ways = 0
        self.accepted_ways = 0
        self.directed_edges = 0

        self.node_buffer = []
        self.edge_buffer = []

        self.insert_node_sql = """
            INSERT OR IGNORE INTO nodes (
                node_id,
                latitude,
                longitude
            )
            VALUES (?, ?, ?)
        """

        self.insert_edge_sql = """
            INSERT INTO edges (
                from_id,
                to_id,
                distance
            )
            VALUES (?, ?, ?)
            ON CONFLICT(from_id, to_id)
            DO UPDATE SET distance = MIN(
                edges.distance,
                excluded.distance
            )
        """

    def add_segment(self, start, end, distance):

        start_id, start_lat, start_lon = start
        end_id, end_lat, end_lon = end

        self.node_buffer.append(
            (start_id, start_lat, start_lon)
        )

        self.node_buffer.append(
            (end_id, end_lat, end_lon)
        )

        self.edge_buffer.append(
            (start_id, end_id, distance)
        )

        self.directed_edges += 1

    def flush_buffers(self):

        if not self.edge_buffer:
            return

        self.connection.executemany(
            self.insert_node_sql,
            self.node_buffer,
        )

        self.connection.executemany(
            self.insert_edge_sql,
            self.edge_buffer,
        )

        self.connection.commit()

        self.node_buffer.clear()
        self.edge_buffer.clear()

    def way(self, way):

        self.total_ways += 1

        highway = way.tags.get("highway")

        if highway not in ROAD_TYPES:
            return

        self.accepted_ways += 1

        oneway = way.tags.get("oneway", "").lower()

        if (
            way.tags.get("junction") == "roundabout"
            and not oneway
        ):
            oneway = "yes"

        reverse_only = oneway in {"-1", "reverse"}

        forward_only = oneway in {"yes", "1", "true"}

        bidirectional = not (
            reverse_only or forward_only
        )

        previous = None

        for node in way.nodes:

            location = node.location

            if not location.valid():
                previous = None
                continue

            lat = location.lat
            lon = location.lon

            # Keep the geographical area within the
            # North-Eastern region bounding box.

            if not (
                MIN_LAT <= lat <= MAX_LAT
                and MIN_LON <= lon <= MAX_LON
            ):
                previous = None
                continue

            current = (
                int(node.ref),
                lat,
                lon,
            )

            if previous is not None:

                distance = haversine(
                    previous[1],
                    previous[2],
                    current[1],
                    current[2],
                )

                if distance > 0:

                    if reverse_only:

                        self.add_segment(
                            current,
                            previous,
                            distance,
                        )

                    else:

                        self.add_segment(
                            previous,
                            current,
                            distance,
                        )

                        if bidirectional:

                            self.add_segment(
                                current,
                                previous,
                                distance,
                            )

            previous = current

            # Write batches to SQLite rather than
            # keeping the whole road network in RAM.

            if len(self.edge_buffer) >= BATCH_SIZE:
                self.flush_buffers()

    def finish(self):
        """Write any remaining records."""

        self.flush_buffers()


# ============================================================
# 5. EXTRACT ROADS FROM THE OSM FILE
# ============================================================

def extract_roads():

    print("\n========== ROAD EXTRACTION ==========")

    if not PBF_FILE.exists():
        raise FileNotFoundError(
            f"OSM PBF file not found:\n{PBF_FILE}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if DATABASE_FILE.exists():
        raise FileExistsError(
            "\nA working SQLite database already exists:\n"
            f"{DATABASE_FILE}\n\n"
            "The script will not overwrite it automatically. "
            "If it contains a completed extraction, use "
            "--build-only. If it is an incomplete extraction, "
            "inspect it before deciding whether to delete it."
        )

    free_space = (
        shutil.disk_usage(OUTPUT_DIR).free
        / (1024 ** 3)
    )

    print(
        f"Available disk space: {free_space:.2f} GB"
    )

    if free_space < 8:
        raise RuntimeError(
            "At least 8 GB of free disk space is recommended "
            "before starting extraction."
        )

    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=120,
    )

    try:

        configure_database(connection)

        set_metadata(
            connection,
            "extraction_complete",
            "0",
        )

        connection.commit()

        handler = RoadNetworkHandler(connection)

        print("\nReading OSM PBF file...")
        print("This may take a considerable amount of time.")

        handler.apply_file(
            str(PBF_FILE),
            locations=True,
            idx="flex_mem",
        )

        handler.finish()

        node_count = connection.execute(
            "SELECT COUNT(*) FROM nodes"
        ).fetchone()[0]

        unique_edge_count = connection.execute(
            "SELECT COUNT(*) FROM edges"
        ).fetchone()[0]

        set_metadata(
            connection,
            "extraction_complete",
            "1",
        )

        connection.commit()

        print("\nExtraction completed successfully.")

        print("Total OSM ways:", handler.total_ways)
        print("Accepted road ways:", handler.accepted_ways)
        print("Directed edge records:", handler.directed_edges)
        print("Unique road nodes:", node_count)
        print("Unique directed edges:", unique_edge_count)

        print(
            "Database size:",
            round(
                DATABASE_FILE.stat().st_size
                / (1024 ** 3),
                2,
            ),
            "GB",
        )

    finally:
        connection.close()


# ============================================================
# 6. CREATE DISK-BACKED ARRAYS
# ============================================================

def create_memmap(filename, dtype, shape):

    return np.lib.format.open_memmap(
        filename,
        mode="w+",
        dtype=dtype,
        shape=shape,
    )


# ============================================================
# 7. BUILD NODE INDEX
# ============================================================

def build_node_index(connection):

    print("\n========== BUILDING NODE INDEX ==========")

    node_count = connection.execute(
        "SELECT COUNT(*) FROM nodes"
    ).fetchone()[0]

    if node_count == 0:
        raise ValueError("No road nodes were found.")

    if node_count >= np.iinfo(np.int32).max:
        raise ValueError(
            "The number of nodes exceeds the supported "
            "32-bit sparse matrix index range."
        )

    node_ids = create_memmap(
        WORK_DIR / "node_ids.npy",
        np.int64,
        (node_count,),
    )

    latitudes = create_memmap(
        WORK_DIR / "latitudes.npy",
        np.float32,
        (node_count,),
    )

    longitudes = create_memmap(
        WORK_DIR / "longitudes.npy",
        np.float32,
        (node_count,),
    )

    cursor = connection.execute("""
        SELECT node_id, latitude, longitude
        FROM nodes
        ORDER BY node_id
    """)

    position = 0

    while True:

        batch = cursor.fetchmany(READ_BATCH_SIZE)

        if not batch:
            break

        updates = []

        for node_id, latitude, longitude in batch:

            node_ids[position] = node_id
            latitudes[position] = latitude
            longitudes[position] = longitude

            updates.append(
                (position, node_id)
            )

            position += 1

        connection.executemany(
            """
            UPDATE nodes
            SET matrix_index = ?
            WHERE node_id = ?
            """,
            updates,
        )

        connection.commit()

        print(
            f"Indexed {position:,} / {node_count:,} nodes",
            end="\r",
        )

    print()

    if position != node_count:
        raise RuntimeError(
            "Node indexing did not finish correctly."
        )

    node_ids.flush()
    latitudes.flush()
    longitudes.flush()

    return (
        node_count,
        node_ids,
        latitudes,
        longitudes,
    )


# ============================================================
# 8. BUILD CSR GRAPH USING DISK-BACKED ARRAYS
# ============================================================

def build_csr_graph(connection, node_count):

    print("\n========== BUILDING SPARSE GRAPH ==========")

    edge_count = connection.execute(
        "SELECT COUNT(*) FROM edges"
    ).fetchone()[0]

    if edge_count == 0:
        raise ValueError("No road edges were found.")

    print("Nodes:", f"{node_count:,}")
    print("Unique directed edges:", f"{edge_count:,}")

    # CSR arrays are created as memory-mapped files.
    # They are not all loaded into ordinary RAM.

    indptr = create_memmap(
        WORK_DIR / "indptr.npy",
        np.int64,
        (node_count + 1,),
    )

    indices = create_memmap(
        WORK_DIR / "indices.npy",
        np.int32,
        (edge_count,),
    )

    data = create_memmap(
        WORK_DIR / "data.npy",
        np.float32,
        (edge_count,),
    )

    indptr[0] = 0

    cursor = connection.execute("""
        SELECT
            source.matrix_index,
            destination.matrix_index,
            edges.distance

        FROM edges

        INNER JOIN nodes AS source
            ON edges.from_id = source.node_id

        INNER JOIN nodes AS destination
            ON edges.to_id = destination.node_id

        ORDER BY edges.from_id, edges.to_id
    """)

    current_row = 0
    position = 0

    print("\nWriting graph edges...")

    while True:

        batch = cursor.fetchmany(READ_BATCH_SIZE)

        if not batch:
            break

        for source_index, target_index, distance in batch:

            source_index = int(source_index)
            target_index = int(target_index)

            # Fill empty rows between consecutive sources.
            while current_row < source_index:

                current_row += 1
                indptr[current_row] = position

            indices[position] = target_index
            data[position] = distance

            position += 1

        print(
            f"Processed {position:,} / {edge_count:,} edges",
            end="\r",
        )

    print()

    # Finish the remaining empty rows.
    while current_row < node_count:

        current_row += 1
        indptr[current_row] = position

    if position != edge_count:
        raise RuntimeError(
            "The number of written edges does not match "
            "the database edge count."
        )

    indptr.flush()
    indices.flush()
    data.flush()

    print("CSR arrays constructed successfully.")

    graph = csr_matrix(
        (data, indices, indptr),
        shape=(node_count, node_count),
        dtype=np.float32,
        copy=False,
    )

    return graph


# ============================================================
# 9. SAVE FINAL GRAPH AND NODE FILES
# ============================================================

def save_graph_files(
    graph,
    node_ids,
    latitudes,
    longitudes,
):

    print("\n========== SAVING OUTPUT FILES ==========")

    print("Saving sparse road graph...")

    # Uncompressed output avoids the additional memory
    # and processing overhead of compression.

    save_npz(
        GRAPH_FILE,
        graph,
        compressed=False,
    )

    print("Road graph saved.")

    print("Saving node coordinates...")

    np.savez(
        NODE_FILE,
        node_ids=node_ids,
        latitudes=latitudes,
        longitudes=longitudes,
    )

    print("Node coordinates saved.")

    print("\nOutput files:")

    print(
        "Graph:",
        GRAPH_FILE,
        f"({GRAPH_FILE.stat().st_size / (1024 ** 3):.2f} GB)",
    )

    print(
        "Nodes:",
        NODE_FILE,
        f"({NODE_FILE.stat().st_size / (1024 ** 3):.2f} GB)",
    )


# ============================================================
# 10. BUILD THE GRAPH FROM THE SQLITE DATABASE
# ============================================================

def build_sparse_graph():

    print("\n========== GRAPH BUILDING ==========")

    if not extraction_is_complete():
        raise RuntimeError(
            "A completed extraction database was not found. "
            "Run the extraction stage first."
        )

    WORK_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=120,
    )

    try:

        configure_database(connection)

        (
            node_count,
            node_ids,
            latitudes,
            longitudes,
        ) = build_node_index(connection)

        graph = build_csr_graph(
            connection,
            node_count,
        )

        save_graph_files(
            graph,
            node_ids,
            latitudes,
            longitudes,
        )

        print("\nVerifying saved graph...")

        if not GRAPH_FILE.exists() or not NODE_FILE.exists():
            raise RuntimeError(
                "One or more output files are missing."
            )

        if graph.shape != (node_count, node_count):
            raise RuntimeError(
                "The saved graph dimensions are incorrect."
            )

        print("Graph dimensions:", graph.shape)
        print("Graph non-zero edges:", graph.nnz)

        print("\nOptimization completed successfully.")

        # Release memory-mapped arrays.
        del graph
        del node_ids
        del latitudes
        del longitudes

        gc.collect()

    finally:
        connection.close()

    # Remove only temporary NumPy work files.
    # Keep the SQLite database as a recovery source.

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)

    print("\nTemporary work arrays removed.")
    print("SQLite working database retained:")
    print(DATABASE_FILE)


# ============================================================
# 11. MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Build an optimized OSM road network."
    )

    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Extract OSM roads into the SQLite database.",
    )

    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build the sparse graph from an existing completed database.",
    )

    args = parser.parse_args()

    if args.extract_only and args.build_only:
        parser.error(
            "Choose either --extract-only or --build-only."
        )

    if args.extract_only:

        extract_roads()
        return

    if args.build_only:

        build_sparse_graph()
        return

    # Default operation:
    # Extract if no database exists, then build the graph.

    if DATABASE_FILE.exists():

        if not extraction_is_complete():
            raise RuntimeError(
                "An incomplete SQLite database already exists. "
                "Do not overwrite it automatically. Inspect or "
                "remove it before starting a fresh extraction."
            )

        print(
            "\nCompleted extraction database found. "
            "Skipping PBF extraction."
        )

    else:

        extract_roads()

    build_sparse_graph()


if __name__ == "__main__":
    main()
