
from django.shortcuts import render

from locations.models import Location
from logistics.alternative_routes import generate_alternative_routes
from logistics.route_ranking import rank_routes, PREFERENCES
from accessibility.intelligence import evaluate_road_connectivity


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
        "alternative_routes": [],
        "route_count": 0,
        "source_accessibility": None,
        "destination_accessibility": None,

        # Route preference information
        "selected_preference": selected_preference,
        "route_preferences": PREFERENCES,
        "ranking_available": False,
        "ranking_message": "",
    }

    if request.method == "POST":

        source_id = request.POST.get("source", "").strip()
        destination_id = request.POST.get("destination", "").strip()

        selected_preference = request.POST.get(
            "preference",
            "shortest"
        ).strip().lower()

        # Prevent invalid preference values.
        if selected_preference not in PREFERENCES:
            selected_preference = "shortest"

        context["source"] = source_id
        context["destination"] = destination_id
        context["selected_preference"] = selected_preference

        try:

            source_location = locations.get(pk=int(source_id))
            destination_location = locations.get(
                pk=int(destination_id)
            )

            context["source_location"] = source_location
            context["destination_location"] = destination_location

            if source_location.pk == destination_location.pk:

                context["error"] = (
                    "Source and destination cannot be the same."
                )

            else:

                # Generate direct and alternative route candidates.
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

                    # Rank the generated routes using the selected preference.
                    ranking_result = rank_routes(
                        alternative_routes,
                        selected_preference,
                    )

                    ranked_routes = ranking_result["routes"]

                    if not ranked_routes:

                        context["error"] = (
                            "No valid routes were available for ranking."
                        )

                    else:

                        # The highest-ranked route becomes the main route.
                        recommended_route = ranked_routes[0]

                        route_result = recommended_route.get(
                            "route_data"
                        )

                        source_accessibility = evaluate_road_connectivity(
                            source_location
                        )

                        destination_accessibility = evaluate_road_connectivity(
                            destination_location
                        )

                        context["route_result"] = route_result
                        context["alternative_routes"] = ranked_routes
                        context["route_count"] = len(ranked_routes)

                        context["source_accessibility"] = (
                            source_accessibility
                        )

                        context["destination_accessibility"] = (
                            destination_accessibility
                        )

                        context["ranking_available"] = (
                            ranking_result["available"]
                        )

                        context["ranking_message"] = (
                            ranking_result["message"]
                        )

                        context["submitted"] = True

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

            context["error"] = (
                "The road network could not be loaded. "
                "Please check the available system resources and data files."
            )

    return render(
        request,
        "logistics/route_planner.html",
        context
    )
