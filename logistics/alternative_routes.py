
from logistics.routing import calculate_road_route, get_routing_engine


MAX_ALTERNATIVES = 3
PENALTY_MULTIPLIERS = (3.0, 5.0, 8.0)
MAX_DETOUR_RATIO = 1.50
MIN_ROUTE_DIFFERENCE_KM = 0.5
MAX_SIMILARITY = 0.90


def _route_similarity(path_a, path_b):
    """Compare two routes using their directed road edges."""
    edges_a = set(zip(path_a, path_a[1:]))
    edges_b = set(zip(path_b, path_b[1:]))

    if not edges_a or not edges_b:
        return 0.0

    shared = len(edges_a.intersection(edges_b))
    total = len(edges_a.union(edges_b))

    return shared / total if total else 0.0


def _build_route_data(engine, path, direct_route):
    """Build the same route-data structure used by the planner."""
    coordinates = [
        {
            "latitude": float(engine.latitudes[index]),
            "longitude": float(engine.longitudes[index]),
        }
        for index in path
    ]

    distance = engine.calculate_path_distance(path)

    return {
        "source_node": direct_route["source_node"],
        "destination_node": direct_route["destination_node"],
        "distance_km": distance,
        "node_count": len(path),
        "coordinates": coordinates,
        "path_indices": path,
    }


def generate_alternative_routes(source, destination, max_hubs=3):
    """
    Generate a direct shortest route and up to three alternatives
    using progressively penalized road edges.

    max_hubs is retained for compatibility with existing callers.
    """
    try:
        direct_route = calculate_road_route(source, destination)
    except (ValueError, KeyError, IndexError):
        return []

    if not direct_route:
        return []

    engine = get_routing_engine()

    direct_path = direct_route["path_indices"]
    direct_distance = direct_route["distance_km"]

    if len(direct_path) < 2 or direct_distance <= 0:
        return []

    routes = [
        {
            "name": "Direct Shortest Route",
            "waypoint": None,
            "distance_km": direct_distance,
            "node_count": direct_route["node_count"],
            "coordinates": direct_route["coordinates"],
            "route_data": direct_route,
        }
    ]

    accepted_paths = [direct_path]
    attempted_paths = set()

    # Start by penalizing the edges used by the shortest route.
    penalized_edges = set(zip(direct_path, direct_path[1:]))

    for multiplier in PENALTY_MULTIPLIERS:
        try:
            result = engine.find_alternative_path_with_penalty(
                direct_path[0],
                direct_path[-1],
                penalized_edges,
                penalty_multiplier=multiplier,
            )
        except (ValueError, KeyError, IndexError):
            continue

        alternative_path = result.get("path_indices")

        if not alternative_path or len(alternative_path) < 2:
            continue

        path_signature = tuple(alternative_path)

        # Avoid processing the same attempted path repeatedly.
        if path_signature in attempted_paths:
            continue

        attempted_paths.add(path_signature)

        # Penalize every attempted path, even if it is rejected.
        # This encourages the next search to explore other roads.
        penalized_edges.update(
            zip(alternative_path, alternative_path[1:])
        )

        alternative_distance = result.get("actual_distance_km")

        if alternative_distance is None:
            continue

        if alternative_distance > direct_distance * MAX_DETOUR_RATIO:
            continue

        if alternative_distance < direct_distance + MIN_ROUTE_DIFFERENCE_KM:
            continue

        too_similar = any(
            _route_similarity(alternative_path, existing_path)
            > MAX_SIMILARITY
            for existing_path in accepted_paths
        )

        if too_similar:
            continue

        route_data = _build_route_data(
            engine,
            alternative_path,
            direct_route,
        )

        routes.append(
            {
                "name": f"Alternative Route {len(routes)}",
                "waypoint": None,
                "distance_km": alternative_distance,
                "node_count": len(alternative_path),
                "coordinates": route_data["coordinates"],
                "route_data": route_data,
            }
        )

        accepted_paths.append(alternative_path)

        if len(routes) >= MAX_ALTERNATIVES + 1:
            break

    return routes
