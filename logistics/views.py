
import logging

from django.shortcuts import render

from locations.models import Location
from logistics.alternative_routes import generate_alternative_routes
from logistics.route_ranking import rank_routes, PREFERENCES

from accessibility.intelligence import evaluate_road_connectivity
from accessibility.risk_engine import assess_route_risk


logger = logging.getLogger(__name__)


def _attach_route_risk(route):
    """
    Attach risk assessment information to a route.

    Only a valid final risk score is exposed as risk_score.
    Provisional scores are never used for route ranking.
    """

    route = dict(route)

    try:
        assessment = assess_route_risk(route)

        final_score = assessment.get("risk_score")

        route.update({
            "risk_score": final_score,

            "risk_score_available": final_score is not None,

            "risk_level": assessment.get(
                "risk_level",
                "insufficient_data"
            ),

            "risk_label": assessment.get(
                "risk_label",
                "Insufficient Data"
            ),

            "risk_color": assessment.get(
                "risk_color",
                "grey"
            ),

            "data_completeness": (
                assessment.get("data_completeness") or 0
            ),

            "provisional_risk_score": assessment.get(
                "provisional_risk_score"
            ),

            "risk_factors": assessment.get(
                "risk_factors",
                {}
            ),

            "risk_message": assessment.get(
                "message",
                "Risk assessment information unavailable."
            ),
        })

        # Never treat a missing final score as zero.
        if final_score is None:
            route["risk_score"] = None
            route["risk_score_available"] = False

    except Exception:
        logger.exception(
            "Risk assessment failed for route: %s",
            route.get("name", "Unknown route")
        )

        route.update({
            "risk_score": None,
            "risk_score_available": False,
            "risk_level": "insufficient_data",
            "risk_label": "Insufficient Data",
            "risk_color": "grey",
            "data_completeness": 0,
            "provisional_risk_score": None,
            "risk_factors": {},
            "risk_message": (
                "Risk assessment could not be completed. "
                "Do not interpret this route as hazard-free."
            ),
        })

    return route


def route_planner(request):

    locations = Location.objects.all().order_by(
        "state",
        "name"
    )

    selected_preference = "shortest"

    context = {
        "locations": locations,

        "source": "",
        "destination": "",

        "submitted": False,
        "error": "",

        "source_location": None,
        "destination_location": None,

        "route_result": None,
        "selected_route": None,

        "alternative_routes": [],
        "route_count": 0,

        "source_accessibility": None,
        "destination_accessibility": None,

        "selected_preference": selected_preference,
        "route_preferences": PREFERENCES,

        "ranking_available": False,
        "ranking_message": "",
    }

    if request.method == "POST":

        source_id = request.POST.get(
            "source",
            ""
        ).strip()

        destination_id = request.POST.get(
            "destination",
            ""
        ).strip()

        selected_preference = request.POST.get(
            "preference",
            "shortest"
        ).strip().lower()

        if selected_preference not in PREFERENCES:
            selected_preference = "shortest"

        context["source"] = source_id
        context["destination"] = destination_id

        context["selected_preference"] = (
            selected_preference
        )

        try:

            # Retrieve selected locations.
            source_location = locations.get(
                pk=int(source_id)
            )

            destination_location = locations.get(
                pk=int(destination_id)
            )

            context["source_location"] = source_location
            context["destination_location"] = destination_location

            # Validate source and destination.
            if source_location.pk == destination_location.pk:

                context["error"] = (
                    "Source and destination cannot be the same."
                )

            else:

                # Generate direct and alternative routes.
                alternative_routes = generate_alternative_routes(
                    source_location,
                    destination_location,
                )

                if not alternative_routes:

                    context["error"] = (
                        "No road route could be calculated between "
                        "the selected locations."
                    )

                else:

                    # Assess risk for every candidate route.
                    assessed_routes = [
                        _attach_route_risk(route)
                        for route in alternative_routes
                    ]

                    # Rank routes using the selected preference.
                    ranking_result = rank_routes(
                        assessed_routes,
                        selected_preference,
                    )

                    ranked_routes = ranking_result["routes"]

                    if not ranked_routes:

                        context["error"] = (
                            "No valid routes were available for ranking."
                        )

                    else:

                        # First route is the selected/recommended route.
                        selected_route = ranked_routes[0]

                        route_result = selected_route.get(
                            "route_data"
                        )

                        # Evaluate accessibility at both endpoints.
                        source_accessibility = (
                            evaluate_road_connectivity(
                                source_location
                            )
                        )

                        destination_accessibility = (
                            evaluate_road_connectivity(
                                destination_location
                            )
                        )

                        # Update page context.
                        context.update({

                            "route_result": route_result,

                            "selected_route": selected_route,

                            "alternative_routes": ranked_routes,

                            "route_count": len(ranked_routes),

                            "source_accessibility": (
                                source_accessibility
                            ),

                            "destination_accessibility": (
                                destination_accessibility
                            ),

                            "ranking_available": (
                                ranking_result["available"]
                            ),

                            "ranking_message": (
                                ranking_result["message"]
                            ),

                            "submitted": True,
                        })

        except (ValueError, Location.DoesNotExist):

            context["error"] = (
                "Please select valid source and destination locations."
            )

        except FileNotFoundError:

            context["error"] = (
                "The road network files could not be found. "
                "Please check the routing data files."
            )

        except OSError:

            logger.exception(
                "Road network loading failed."
            )

            context["error"] = (
                "The road network could not be loaded. "
                "Please check the available system resources "
                "and data files."
            )

    return render(
        request,
        "logistics/route_planner.html",
        context
    )
