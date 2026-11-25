
"""
Accessibility Intelligence Engine
NE-LogiSmart

Core scoring utilities for evaluating logistics accessibility.
"""

from logistics.routing import get_routing_engine


# Accessibility factor weights (must total 1.0)
ACCESSIBILITY_WEIGHTS = {
    "road_connectivity": 0.35,
    "transport_availability": 0.25,
    "route_reliability": 0.20,
    "distance_accessibility": 0.20,
}


def validate_score(score):
    """
    Validate that an individual accessibility score
    is numeric and lies between 0 and 100.
    """
    if score is None:
        return False

    if isinstance(score, bool):
        return False

    if not isinstance(score, (int, float)):
        return False

    if not 0 <= score <= 100:
        return False

    return True


def get_accessibility_rating(score):
    """
    Convert a numerical accessibility score into a rating.
    """
    if not validate_score(score):
        return "Insufficient Data"

    if score >= 80:
        return "Excellent"
    elif score >= 60:
        return "Good"
    elif score >= 40:
        return "Moderate"
    elif score >= 20:
        return "Low"
    else:
        return "Very Low"


def calculate_accessibility_score(scores):
    """
    Calculate a weighted accessibility score.

    Missing factors are excluded, and the remaining
    weights are normalized automatically.

    Args:
        scores (dict): Accessibility factor scores (0-100).

    Returns:
        dict: Score, rating, and data completeness.
    """
    if not isinstance(scores, dict):
        raise TypeError("Scores must be provided as a dictionary.")

    weighted_total = 0
    available_weight = 0
    available_factors = []

    for factor, weight in ACCESSIBILITY_WEIGHTS.items():
        score = scores.get(factor)

        if not validate_score(score):
            continue

        weighted_total += score * weight
        available_weight += weight
        available_factors.append(factor)

    if available_weight == 0:
        return {
            "score": None,
            "rating": "Insufficient Data",
            "data_completeness": 0,
            "available_factors": [],
        }

    final_score = weighted_total / available_weight

    return {
        "score": round(final_score, 2),
        "rating": get_accessibility_rating(final_score),
        "data_completeness": round(available_weight * 100, 2),
        "available_factors": available_factors,
    }


def evaluate_road_connectivity(location):
    """
    Evaluate how close a location is to the extracted road network.

    Uses the shared NE-LogiSmart road routing engine.

    Args:
        location: Location model instance containing latitude
                 and longitude.

    Returns:
        dict: Road snapping information and connectivity score.
    """

    engine = get_routing_engine()

    nearest_node = engine.find_nearest_node(
        location.latitude,
        location.longitude,
    )

    snapping_distance = nearest_node["snapping_distance_km"]

    if snapping_distance <= 0.5:
        score = 100
    elif snapping_distance <= 2:
        score = 80
    elif snapping_distance <= 5:
        score = 60
    elif snapping_distance <= 10:
        score = 40
    else:
        score = 20

    return {
        "location": str(location),
        "nearest_road_node_id": nearest_node["node_id"],
        "snapping_distance_km": round(snapping_distance, 3),
        "road_connectivity_score": score,
        "connectivity_rating": get_accessibility_rating(score),
    }
