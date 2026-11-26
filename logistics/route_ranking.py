
"""
Route preference ranking for NE-LogiSmart.

Only ranks a preference when the required route metrics
are available. Missing metrics are never treated as zero.
"""

PREFERENCES = {
    "shortest": "Shortest",
    "fastest": "Fastest",
    "cheapest": "Cheapest",
    "safest": "Safest",
    "balanced": "Balanced",
}


REQUIRED_METRICS = {
    "shortest": ("distance_km",),
    "fastest": ("estimated_time_hours",),
    "cheapest": ("estimated_cost_inr",),
    "safest": ("risk_score",),
}


def _has_metrics(route, metrics):
    return all(
        route.get(metric) is not None
        for metric in metrics
    )


def rank_routes(routes, preference="shortest"):
    """
    Return routes ordered by the selected preference.

    Risk score must use a consistent scale where lower means safer.
    Balanced ranking uses only metrics available for every route.
    """
    if preference not in PREFERENCES:
        raise ValueError("Invalid route preference.")

    routes = [dict(route) for route in routes]

    if not routes:
        return {
            "routes": [],
            "preference": preference,
            "preference_label": PREFERENCES[preference],
            "available": False,
            "message": "No routes are available.",
        }

    if preference != "balanced":
        metrics = REQUIRED_METRICS[preference]

        if not all(_has_metrics(route, metrics) for route in routes):
            return {
                "routes": routes,
                "preference": preference,
                "preference_label": PREFERENCES[preference],
                "available": False,
                "message": (
                    f"{PREFERENCES[preference]} ranking is unavailable "
                    "because required route data is missing."
                ),
            }

        routes.sort(key=lambda route: route[metrics[0]])

    else:
        balanced_metrics = [
            metric
            for metric in (
                "distance_km",
                "estimated_time_hours",
                "estimated_cost_inr",
                "risk_score",
            )
            if all(route.get(metric) is not None for route in routes)
        ]

        if not balanced_metrics:
            return {
                "routes": routes,
                "preference": preference,
                "preference_label": PREFERENCES[preference],
                "available": False,
                "message": (
                    "Balanced ranking requires at least one metric "
                    "available for every route."
                ),
            }

        # Normalize each metric so different units can be compared.
        scores = []

        for metric in balanced_metrics:
            values = [route[metric] for route in routes]
            minimum = min(values)
            maximum = max(values)

            normalized = [
                0.0 if maximum == minimum
                else (value - minimum) / (maximum - minimum)
                for value in values
            ]

            scores.append(normalized)

        for index, route in enumerate(routes):
            route["_ranking_score"] = sum(
                metric_scores[index] for metric_scores in scores
            ) / len(scores)

        routes.sort(key=lambda route: route["_ranking_score"])

        for route in routes:
            route.pop("_ranking_score", None)

    return {
        "routes": routes,
        "preference": preference,
        "preference_label": PREFERENCES[preference],
        "available": True,
        "message": f"Routes ranked by {PREFERENCES[preference].lower()}.",
    }
