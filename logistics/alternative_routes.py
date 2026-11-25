import math

from locations.models import Location
from logistics.routing import calculate_road_route


# Verified major hubs in the Northeastern Region.
# IDs refer to records in our Location database.
MAJOR_HUBS = [
    {"id": 215, "name": "Bomdila", "state": "Arunachal Pradesh"},
    {"id": 183, "name": "Itanagar", "state": "Arunachal Pradesh"},
    {"id": 216, "name": "Tawang", "state": "Arunachal Pradesh"},
    {"id": 196, "name": "Ziro", "state": "Arunachal Pradesh"},
    {"id": 1081, "name": "Pasighat", "state": "Arunachal Pradesh"},
    {"id": 251, "name": "Naharlagun", "state": "Arunachal Pradesh"},

    {"id": 211, "name": "Dibrugarh", "state": "Assam"},
    {"id": 285, "name": "Guwahati", "state": "Assam"},
    {"id": 81, "name": "Haflong", "state": "Assam"},
    {"id": 597, "name": "Jorhat", "state": "Assam"},
    {"id": 291, "name": "Silchar", "state": "Assam"},
    {"id": 157, "name": "Tezpur", "state": "Assam"},

    {"id": 876, "name": "Churachandpur", "state": "Manipur"},
    {"id": 293, "name": "Imphal", "state": "Manipur"},
    {"id": 1071, "name": "Moreh", "state": "Manipur"},
    {"id": 541, "name": "Ukhrul", "state": "Manipur"},

    {"id": 96, "name": "Jowai", "state": "Meghalaya"},
    {"id": 1465, "name": "Nongpoh", "state": "Meghalaya"},
    {"id": 105, "name": "Shillong", "state": "Meghalaya"},
    {"id": 692, "name": "Tura", "state": "Meghalaya"},

    {"id": 35, "name": "Aizawl", "state": "Mizoram"},
    {"id": 28, "name": "Champhai", "state": "Mizoram"},
    {"id": 684, "name": "Kolasib", "state": "Mizoram"},
    {"id": 11, "name": "Lunglei", "state": "Mizoram"},

    {"id": 1855, "name": "Dimapur", "state": "Nagaland"},
    {"id": 828, "name": "Kohima", "state": "Nagaland"},
    {"id": 634, "name": "Mokokchung", "state": "Nagaland"},
    {"id": 163, "name": "Mon", "state": "Nagaland"},

    {"id": 214, "name": "Gangtok", "state": "Sikkim"},
    {"id": 276, "name": "Gyalshing", "state": "Sikkim"},
    {"id": 186, "name": "Namchi", "state": "Sikkim"},
    {"id": 185, "name": "Rangpo", "state": "Sikkim"},

    {"id": 39, "name": "Agartala", "state": "Tripura"},
    {"id": 336, "name": "Dharmanagar", "state": "Tripura"},
    {"id": 27, "name": "Udaipur", "state": "Tripura"},
]


def haversine_distance(lat1, lon1, lat2, lon2):
    """Return straight-line distance in kilometers."""

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

    a = min(1.0, max(0.0, a))

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_km * c


def select_waypoint_hubs(source, destination, max_hubs=3):
    """
    Select intermediate hubs near the journey midpoint.

    source and destination must be Django Location objects.

    Returns a list of Location objects sorted by midpoint proximity.
    """

    midpoint_lat = (source.latitude + destination.latitude) / 2
    midpoint_lon = (source.longitude + destination.longitude) / 2

    source_destination_ids = {source.id, destination.id}

    hub_ids = [
        hub["id"]
        for hub in MAJOR_HUBS
        if hub["id"] not in source_destination_ids
    ]

    database_hubs = Location.objects.filter(id__in=hub_ids)

    candidates = []

    for hub in database_hubs:

        midpoint_distance = haversine_distance(
            midpoint_lat,
            midpoint_lon,
            hub.latitude,
            hub.longitude,
        )

        source_distance = haversine_distance(
            source.latitude,
            source.longitude,
            hub.latitude,
            hub.longitude,
        )

        destination_distance = haversine_distance(
            destination.latitude,
            destination.longitude,
            hub.latitude,
            hub.longitude,
        )

        # Avoid hubs that are almost the same as the source
        # or destination, as they may produce duplicate routes.
        if source_distance < 20 or destination_distance < 20:
            continue

        candidates.append((midpoint_distance, hub))

    # Closest hubs to the journey midpoint come first.
    candidates.sort(key=lambda item: item[0])

    return [
        hub
        for midpoint_distance, hub in candidates[:max_hubs]
    ]



def calculate_route_via_hub(source, hub, destination):
    """
    Calculate a road route from source to destination through a waypoint hub.

    Returns None if either road segment cannot be calculated.
    """

    try:
        first_segment = calculate_road_route(source, hub)
        second_segment = calculate_road_route(hub, destination)

    except (ValueError, KeyError, IndexError):
        return None

    if not first_segment or not second_segment:
        return None

    first_coordinates = first_segment["coordinates"]
    second_coordinates = second_segment["coordinates"]

    if not first_coordinates or not second_coordinates:
        return None

    # Combine the coordinates, avoiding duplicate junction coordinate.
    combined_coordinates = first_coordinates + second_coordinates[1:]

    total_distance = (
        first_segment["distance_km"]
        + second_segment["distance_km"]
    )

    return {
        "source": source,
        "destination": destination,
        "waypoint": hub,
        "distance_km": total_distance,
        "node_count": (
            first_segment["node_count"]
            + second_segment["node_count"]
            - 1
        ),
        "coordinates": combined_coordinates,
        "segments": [first_segment, second_segment],
    }


def generate_alternative_routes(source, destination, max_hubs=3):
    """
    Generate candidate routes:
    1. Direct shortest road route.
    2. Routes through selected intermediate hubs.

    Routes that are duplicate or excessive detours are filtered out.
    """

    candidates = []

    # Candidate 1: Direct shortest route.
    try:
        direct_route = calculate_road_route(source, destination)
    except (ValueError, KeyError, IndexError):
        direct_route = None

    if not direct_route:
        return []

    candidates.append({
        "name": "Direct Shortest Route",
        "waypoint": None,
        "distance_km": direct_route["distance_km"],
        "node_count": direct_route["node_count"],
        "coordinates": direct_route["coordinates"],
        "route_data": direct_route,
    })

    # Find potential intermediate hubs.
    hubs = select_waypoint_hubs(
        source,
        destination,
        max_hubs=max_hubs,
    )

    direct_distance = direct_route["distance_km"]

    for hub in hubs:

        route = calculate_route_via_hub(
            source,
            hub,
            destination,
        )

        if route is None:
            continue

        alternative_distance = route["distance_km"]

        # Reject routes that are excessively long compared with
        # the direct shortest route. (This is a simple first-pass
        # detour filter, not a traffic-aware optimization.)
        if alternative_distance > direct_distance * 1.50:
            continue

        # Reject alternatives that offer negligible distance difference.
        if alternative_distance <= direct_distance * 1.01:
            continue

        # Reject duplicate waypoint routes with the same distance
        # and nearly identical coordinate count.
        duplicate = any(
            abs(existing["distance_km"] - alternative_distance) < 0.5
            and abs(
                existing["node_count"] - route["node_count"]
            ) < 100
            for existing in candidates
        )

        if duplicate:
            continue

        candidates.append({
            "name": f"Route via {hub.name}",
            "waypoint": hub,
            "distance_km": alternative_distance,
            "node_count": route["node_count"],
            "coordinates": route["coordinates"],
            "route_data": route,
        })

    return candidates