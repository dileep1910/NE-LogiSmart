
"""
NE-LogiSmart Route Risk Engine

Evaluates route risk using:
1. Historical GSI landslide inventory.
2. Fresh Open-Meteo weather observations.
3. Active, geolocated hazard and incident reports.

Important:
- Historical landslides are not active incidents.
- Historical proximity is not a prediction of future landslides.
- Missing data is never interpreted as zero risk.
- Risk thresholds are initial project heuristics, not calibrated
  scientific probabilities or safety guarantees.
"""

import math
from datetime import timedelta

import numpy as np
from scipy.spatial import cKDTree

from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import (
    HistoricalLandslide,
    WeatherObservation,
    HazardObservation,
)


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

EARTH_RADIUS_KM = 6371.0088

LANDSLIDE_SEARCH_RADIUS_KM = 5.0
HAZARD_SEARCH_RADIUS_KM = 5.0
WEATHER_SEARCH_RADIUS_KM = 25.0

WEATHER_MAX_AGE_HOURS = 3

# Limit spatial calculations for long routes.
MAX_ROUTE_SAMPLE_POINTS = 500

RISK_WEIGHTS = {
    "historical_landslide": 0.35,
    "weather": 0.30,
    "active_hazards": 0.35,
}

# A final risk score requires at least 50% weighted evidence.
MINIMUM_DATA_COMPLETENESS = 50.0

LANDSLIDE_THRESHOLDS = (
    (0.25, 100),
    (0.50, 85),
    (1.00, 70),
    (3.00, 45),
    (5.00, 20),
)

SEVERITY_SCORES = {
    "low": 25,
    "moderate": 50,
    "high": 75,
    "critical": 100,
}


# ---------------------------------------------------------
# SPATIAL INDEX CACHE
# ---------------------------------------------------------

_landslide_tree = None
_landslide_metadata = None


# ---------------------------------------------------------
# COORDINATE UTILITIES
# ---------------------------------------------------------

def _validate_coordinate(latitude, longitude):
    """Validate geographic coordinates."""

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None

    return latitude, longitude


def _coordinates_to_xyz(coordinates):
    """
    Convert latitude/longitude coordinates to 3D unit-sphere
    coordinates for spatial indexing.
    """

    radians = np.radians(np.asarray(coordinates, dtype=float))

    latitudes = radians[:, 0]
    longitudes = radians[:, 1]

    x = np.cos(latitudes) * np.cos(longitudes)
    y = np.cos(latitudes) * np.sin(longitudes)
    z = np.sin(latitudes)

    return np.column_stack((x, y, z))


def _chord_to_km(chord_distance):
    """Convert unit-sphere chord distance to great-circle distance."""

    chord_distance = np.clip(chord_distance, 0.0, 2.0)

    angle = 2.0 * np.arcsin(chord_distance / 2.0)

    return angle * EARTH_RADIUS_KM


def _km_to_chord(distance_km):
    """Convert a great-circle radius to unit-sphere chord distance."""

    angle = distance_km / EARTH_RADIUS_KM

    return 2.0 * math.sin(angle / 2.0)


def _sample_route_coordinates(coordinates):
    """
    Reduce very long routes to evenly spaced sample points.

    This limits spatial query cost. Coverage percentages are
    approximate because they use sampled route points.
    """

    if len(coordinates) <= MAX_ROUTE_SAMPLE_POINTS:
        return coordinates

    indices = np.linspace(
        0,
        len(coordinates) - 1,
        MAX_ROUTE_SAMPLE_POINTS,
        dtype=int,
    )

    return [coordinates[index] for index in indices]


def _extract_route_coordinates(route):
    """Accept a route dictionary or route_data dictionary."""

    if not isinstance(route, dict):
        return []

    route_data = route.get("route_data", route)

    if not isinstance(route_data, dict):
        return []

    raw_coordinates = route_data.get("coordinates", [])

    if not isinstance(raw_coordinates, (list, tuple)):
        return []

    valid_coordinates = []

    for item in raw_coordinates:
        if not isinstance(item, dict):
            continue

        coordinate = _validate_coordinate(
            item.get("latitude"),
            item.get("longitude"),
        )

        if coordinate is not None:
            valid_coordinates.append(coordinate)

    return valid_coordinates


# ---------------------------------------------------------
# HISTORICAL LANDSLIDE SPATIAL INDEX
# ---------------------------------------------------------

def _get_landslide_index():
    """
    Build and cache the GSI landslide spatial index.

    Refresh it if record count or latest import timestamp changes.
    """

    global _landslide_tree
    global _landslide_metadata

    metadata = HistoricalLandslide.objects.aggregate(
        total=Count("id"),
        latest_import=Max("imported_at"),
    )

    current_signature = (
        metadata["total"],
        metadata["latest_import"],
    )

    if (
        _landslide_tree is not None
        and current_signature == _landslide_metadata
    ):
        return _landslide_tree

    records = list(
        HistoricalLandslide.objects.values_list(
            "latitude",
            "longitude",
        )
    )

    if not records:
        _landslide_tree = None
        _landslide_metadata = current_signature
        return None

    valid_points = []

    for latitude, longitude in records:
        coordinate = _validate_coordinate(latitude, longitude)

        if coordinate is not None:
            valid_points.append(coordinate)

    if not valid_points:
        _landslide_tree = None
        _landslide_metadata = current_signature
        return None

    points = np.asarray(valid_points, dtype=float)

    _landslide_tree = cKDTree(_coordinates_to_xyz(points))
    _landslide_metadata = current_signature

    return _landslide_tree


# ---------------------------------------------------------
# HISTORICAL LANDSLIDE RISK
# ---------------------------------------------------------

def assess_historical_landslide_risk(route_coordinates):
    """
    Calculate historical landslide exposure along a route.

    Counts distinct GSI inventory records within the search radius.
    A record close to multiple route points is counted only once.

    The score uses the nearest historical record as its proximity
    indicator. Record counts are reported separately because the
    inventory does not provide calibrated route-risk probabilities.
    """

    if not route_coordinates:
        return {
            "available": False,
            "score": None,
            "nearest_distance_km": None,
            "historical_records": 0,
            "records_within_radius": None,
            "message": "Route coordinates are unavailable.",
        }

    tree = _get_landslide_index()

    if tree is None:
        return {
            "available": False,
            "score": None,
            "nearest_distance_km": None,
            "historical_records": 0,
            "records_within_radius": None,
            "message": "No usable historical landslide records are available.",
        }

    sampled_coordinates = _sample_route_coordinates(route_coordinates)
    route_xyz = _coordinates_to_xyz(sampled_coordinates)

    distances, _ = tree.query(route_xyz, k=1)

    nearest_distance_km = float(
        np.min(_chord_to_km(distances))
    )

    radius_chord = _km_to_chord(LANDSLIDE_SEARCH_RADIUS_KM)

    # Each inventory record is counted once even if it is near
    # multiple sampled coordinates along the route.
    nearby_record_indices = set()

    matches = tree.query_ball_point(
        route_xyz,
        r=radius_chord,
    )

    for matching_indices in matches:
        nearby_record_indices.update(matching_indices)

    records_within_radius = len(nearby_record_indices)

    proximity_score = 0

    for threshold, score in LANDSLIDE_THRESHOLDS:
        if nearest_distance_km <= threshold:
            proximity_score = score
            break

    return {
        "available": True,
        "score": proximity_score,
        "nearest_distance_km": round(nearest_distance_km, 3),
        "historical_records": _landslide_metadata[0],
        "records_within_radius": records_within_radius,
        "search_radius_km": LANDSLIDE_SEARCH_RADIUS_KM,
        "sample_points": len(sampled_coordinates),
        "message": (
            "Historical GSI inventory proximity evaluated. "
            f"{records_within_radius} distinct historical records "
            f"were found within {LANDSLIDE_SEARCH_RADIUS_KM} km "
            "of sampled route coordinates. This is not evidence "
            "of an active landslide or a calibrated prediction."
        ),
    }


# ---------------------------------------------------------
# WEATHER RISK
# ---------------------------------------------------------

def _weather_observation_score(observation):
    """
    Calculate a preliminary weather risk indicator.

    Thresholds are project heuristics. Verify provider units
    before interpreting or displaying measurements.
    """

    scores = []

    rainfall = observation.rainfall_mm

    if rainfall is not None:
        if rainfall >= 50:
            scores.append(100)
        elif rainfall >= 20:
            scores.append(80)
        elif rainfall >= 7.5:
            scores.append(60)
        elif rainfall >= 2.5:
            scores.append(40)
        elif rainfall > 0:
            scores.append(20)
        else:
            scores.append(0)

    wind_speed = observation.wind_speed

    if wind_speed is not None:
        if wind_speed >= 60:
            scores.append(100)
        elif wind_speed >= 40:
            scores.append(75)
        elif wind_speed >= 25:
            scores.append(50)
        elif wind_speed >= 15:
            scores.append(25)
        else:
            scores.append(0)

    visibility = observation.visibility_km

    if visibility is not None:
        if visibility < 0.5:
            scores.append(100)
        elif visibility < 1:
            scores.append(75)
        elif visibility < 2:
            scores.append(50)
        elif visibility < 5:
            scores.append(25)
        else:
            scores.append(0)

    if not scores:
        return None

    return max(scores)


def assess_weather_risk(route_coordinates):
    """
    Evaluate fresh weather observations near the route.

    Returns a route-sample coverage percentage. A weather
    observation at one endpoint must not be presented as
    representative of the entire route.
    
    No external API request is made here.
    """

    if not route_coordinates:
        return {
            "available": False,
            "score": None,
            "observations_used": 0,
            "coverage_percent": 0,
            "message": "Route coordinates are unavailable.",
        }

    now = timezone.now()

    observations = WeatherObservation.objects.filter(
        observed_at__gte=now - timedelta(hours=WEATHER_MAX_AGE_HOURS),
        observed_at__lte=now,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    ).exclude(
        latitude__isnull=True
    ).exclude(
        longitude__isnull=True
    )

    observation_points = []
    observation_scores = []

    for observation in observations:
        coordinate = _validate_coordinate(
            observation.latitude,
            observation.longitude,
        )

        if coordinate is None:
            continue

        score = _weather_observation_score(observation)

        if score is None:
            continue

        observation_points.append(coordinate)
        observation_scores.append(score)

    if not observation_points:
        return {
            "available": False,
            "score": None,
            "observations_used": 0,
            "coverage_percent": 0,
            "message": "No fresh usable weather observations are available.",
        }

    sampled_coordinates = _sample_route_coordinates(route_coordinates)

    route_xyz = _coordinates_to_xyz(sampled_coordinates)
    observation_xyz = _coordinates_to_xyz(observation_points)

    observation_tree = cKDTree(observation_xyz)

    # Find the nearest observation for every sampled route point.
    distances, nearest_observation_indices = observation_tree.query(
        route_xyz,
        k=1,
    )

    distances_km = _chord_to_km(distances)

    covered_mask = distances_km <= WEATHER_SEARCH_RADIUS_KM

    covered_count = int(np.count_nonzero(covered_mask))

    coverage_percent = round(
        (covered_count / len(sampled_coordinates)) * 100,
        2,
    )

    if covered_count == 0:
        return {
            "available": False,
            "score": None,
            "observations_used": 0,
            "coverage_percent": 0,
            "message": (
                "Fresh observations exist, but none are close "
                "enough to represent this route."
            ),
        }

    relevant_indices = set(
        int(index)
        for index in nearest_observation_indices[covered_mask]
    )

    relevant_scores = [
        observation_scores[index]
        for index in relevant_indices
    ]

    return {
        "available": True,
        "score": max(relevant_scores),
        "observations_used": len(relevant_indices),
        "coverage_percent": coverage_percent,
        "sample_points": len(sampled_coordinates),
        "covered_sample_points": covered_count,
        "search_radius_km": WEATHER_SEARCH_RADIUS_KM,
        "message": (
            "Fresh model-based weather observations evaluated. "
            f"Approximate sampled route coverage: {coverage_percent}%. "
            "Uncovered portions of the route have unknown conditions."
        ),
    }


# ---------------------------------------------------------
# ACTIVE HAZARD RISK
# ---------------------------------------------------------

def assess_active_hazard_risk(route_coordinates):
    """
    Detect active, non-expired, geolocated hazards near the route.

    An empty result is unavailable, NOT zero risk.

    A zero score is returned only when at least one recorded
    active hazard is found within the search radius and its
    severity/proximity calculation produces a non-zero score.
    If no hazard is found near the route, coverage is unknown.
    """

    if not route_coordinates:
        return {
            "available": False,
            "score": None,
            "hazards_found": 0,
            "message": "Route coordinates are unavailable.",
        }

    now = timezone.now()

    hazards = HazardObservation.objects.filter(
        status=HazardObservation.Status.ACTIVE,
        latitude__isnull=False,
        longitude__isnull=False,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )

    valid_hazards = []

    for hazard in hazards:
        coordinate = _validate_coordinate(
            hazard.latitude,
            hazard.longitude,
        )

        if coordinate is not None:
            valid_hazards.append((hazard, coordinate))

    if not valid_hazards:
        return {
            "available": False,
            "score": None,
            "hazards_found": 0,
            "message": (
                "No usable active geolocated hazard reports are "
                "available. Incident coverage is unknown; this "
                "must not be interpreted as a hazard-free route."
            ),
        }

    sampled_coordinates = _sample_route_coordinates(route_coordinates)

    route_xyz = _coordinates_to_xyz(sampled_coordinates)
    route_tree = cKDTree(route_xyz)

    hazard_coordinates = [
        coordinate
        for _, coordinate in valid_hazards
    ]

    hazard_xyz = _coordinates_to_xyz(hazard_coordinates)

    distances, _ = route_tree.query(hazard_xyz, k=1)
    distances_km = _chord_to_km(distances)

    nearby_scores = []

    for index, distance in enumerate(distances_km):
        if distance > HAZARD_SEARCH_RADIUS_KM:
            continue

        hazard = valid_hazards[index][0]

        severity_score = SEVERITY_SCORES.get(
            hazard.severity,
            50,
        )

        if distance <= 0.5:
            proximity_multiplier = 1.0
        elif distance <= 1:
            proximity_multiplier = 0.85
        elif distance <= 2:
            proximity_multiplier = 0.65
        else:
            proximity_multiplier = 0.40

        nearby_scores.append(
            min(100, round(severity_score * proximity_multiplier))
        )

    if not nearby_scores:
        return {
            "available": False,
            "score": None,
            "hazards_found": 0,
            "message": (
                "No recorded active hazards were found near this route. "
                "However, incident reporting coverage is not established, "
                "so the absence of reports cannot establish safety."
            ),
        }

    return {
        "available": True,
        "score": max(nearby_scores),
        "hazards_found": len(nearby_scores),
        "search_radius_km": HAZARD_SEARCH_RADIUS_KM,
        "message": (
            "Recorded active hazards detected near the route. "
            "This result reflects available reports, not complete "
            "regional incident coverage."
        ),
    }


# ---------------------------------------------------------
# RISK CLASSIFICATION
# ---------------------------------------------------------

def classify_route_risk(score):
    """Convert a final risk score into a classification."""

    if score is None:
        return {
            "level": "insufficient_data",
            "label": "Insufficient Data",
            "color": "grey",
        }

    if score <= 30:
        return {
            "level": "low",
            "label": "Low Risk",
            "color": "green",
        }

    if score <= 60:
        return {
            "level": "moderate",
            "label": "Moderate Risk",
            "color": "orange",
        }

    return {
        "level": "high",
        "label": "High Risk",
        "color": "red",
    }


# ---------------------------------------------------------
# COMPLETE ROUTE RISK ASSESSMENT
# ---------------------------------------------------------

def assess_route_risk(route):
    """
    Calculate a preliminary route risk assessment.

    Missing factors are excluded from the weighted calculation.
    Weather contributes in proportion to its approximate spatial
    coverage. A final classification is withheld when weighted
    evidence availability is below the configured minimum.

    Args:
        route:
            Alternative route dictionary or route_data dictionary.

    Returns:
        Risk score, classification, evidence completeness,
        individual factors and explanatory messages.
    """

    route_coordinates = _extract_route_coordinates(route)

    if len(route_coordinates) < 2:
        return {
            "risk_score": None,
            "provisional_risk_score": None,
            "risk_level": "insufficient_data",
            "risk_label": "Insufficient Data",
            "risk_color": "grey",
            "data_completeness": 0,
            "risk_factors": {},
            "message": "Insufficient route coordinates.",
        }

    factor_results = {
        "historical_landslide": assess_historical_landslide_risk(
            route_coordinates
        ),
        "weather": assess_weather_risk(route_coordinates),
        "active_hazards": assess_active_hazard_risk(
            route_coordinates
        ),
    }

    weighted_total = 0.0
    available_weight = 0.0

    for factor, base_weight in RISK_WEIGHTS.items():
        result = factor_results[factor]

        if not result["available"]:
            continue

        score = result["score"]

        if score is None:
            continue

        effective_weight = base_weight

        # Weather evidence is limited to the approximate
        # proportion of sampled route points covered.
        if factor == "weather":
            coverage_fraction = (
                result.get("coverage_percent", 0) / 100
            )

            effective_weight *= coverage_fraction

        if effective_weight <= 0:
            continue

        weighted_total += score * effective_weight
        available_weight += effective_weight

    completeness = round(
        available_weight * 100,
        2,
    )

    provisional_score = None

    if available_weight > 0:
        provisional_score = round(
            weighted_total / available_weight,
            2,
        )

    # Do not publish a final risk classification from weak evidence.
    if completeness < MINIMUM_DATA_COMPLETENESS:
        classification = classify_route_risk(None)

        return {
            "risk_score": None,
            "provisional_risk_score": provisional_score,
            "risk_level": classification["level"],
            "risk_label": classification["label"],
            "risk_color": classification["color"],
            "data_completeness": completeness,
            "minimum_required_completeness": (
                MINIMUM_DATA_COMPLETENESS
            ),
            "risk_factors": factor_results,
            "message": (
                "Insufficient evidence for a final route risk "
                "classification. A provisional score, if present, "
                "is diagnostic only and must not be used to rank "
                "routes as safe or unsafe."
            ),
        }

    final_score = provisional_score
    classification = classify_route_risk(final_score)

    return {
        "risk_score": final_score,
        "provisional_risk_score": final_score,
        "risk_level": classification["level"],
        "risk_label": classification["label"],
        "risk_color": classification["color"],
        "data_completeness": completeness,
        "minimum_required_completeness": (
            MINIMUM_DATA_COMPLETENESS
        ),
        "risk_factors": factor_results,
        "message": (
            "Preliminary route risk calculated using available "
            "evidence. Data completeness measures weighted factor "
            "availability, not verified geographic coverage. "
            "This estimate is not a safety guarantee."
        ),
    }
