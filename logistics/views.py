
from django.shortcuts import render

from locations.models import Location
from logistics.routing import calculate_road_route
from accessibility.intelligence import evaluate_road_connectivity


def route_planner(request):

    locations = Location.objects.all().order_by(
        "state",
        "name"
    )

    context = {
        "locations": locations,
        "source": "",
        "destination": "",
        "submitted": False,
        "error": "",
        "source_location": None,
        "destination_location": None,
        "route_result": None,
        "source_accessibility": None,
        "destination_accessibility": None,
    }

    if request.method == "POST":

        source_id = request.POST.get("source", "").strip()
        destination_id = request.POST.get("destination", "").strip()

        context["source"] = source_id
        context["destination"] = destination_id

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

                route_result = calculate_road_route(
                    source_location,
                    destination_location
                )

                source_accessibility = evaluate_road_connectivity(
                    source_location
                )

                destination_accessibility = evaluate_road_connectivity(
                    destination_location
                )

                context["route_result"] = route_result
                context["source_accessibility"] = source_accessibility
                context["destination_accessibility"] = (
                    destination_accessibility
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
