
import json
from datetime import timedelta, timezone as datetime_timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import WeatherObservation


API_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Severe thunderstorm with hail",
}


class WeatherServiceError(Exception):
    """Raised when weather data cannot be retrieved or processed."""


def parse_provider_datetime(value, provider_timezone):
    """
    Parse a provider timestamp and attach the provider's timezone
    when the timestamp does not already contain one.
    """

    parsed = parse_datetime(value)

    if parsed is None:
        return None

    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=provider_timezone)

    return parsed


def fetch_and_store_weather(location):
    """
    Fetch model-based current weather estimates for a Location
    instance and save them in the database.

    Open-Meteo is a supplementary source, not an official
    IMD weather observation station.
    """

    latitude = location.latitude
    longitude = location.longitude

    if not (-90 <= latitude <= 90):
        raise WeatherServiceError("Invalid latitude.")

    if not (-180 <= longitude <= 180):
        raise WeatherServiceError("Invalid longitude.")

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,"
            "precipitation,"
            "wind_speed_10m,"
            "weather_code"
        ),
        "hourly": "visibility",
        "forecast_hours": 1,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }

    request_url = f"{API_URL}?{urlencode(params)}"

    request = Request(
        request_url,
        headers={"User-Agent": "NE-LogiSmart/1.0"},
    )

    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise WeatherServiceError(
            f"Weather API request failed: {error}"
        ) from error

    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WeatherServiceError(
            "Invalid response received from weather provider."
        ) from error

    current = data.get("current")

    if not current:
        raise WeatherServiceError(
            "Weather provider returned no current weather data."
        )

    # Open-Meteo supplies the local UTC offset in seconds.
    offset_seconds = data.get("utc_offset_seconds", 0)

    provider_timezone = datetime_timezone(
        timedelta(seconds=offset_seconds)
    )

    observed_at = parse_provider_datetime(
        current.get("time", ""),
        provider_timezone,
    )

    if observed_at is None:
        raise WeatherServiceError(
            "Weather observation timestamp is missing or invalid."
        )

    weather_code = current.get("weather_code")

    weather_condition = WEATHER_CODES.get(
        weather_code,
        "Unknown"
    )

    # Visibility is supplied in metres by Open-Meteo.
    visibility_km = None

    hourly = data.get("hourly", {})
    hourly_times = hourly.get("time", [])
    visibility_values = hourly.get("visibility", [])

    if hourly_times and visibility_values:
        valid_visibility = []

        for time_value, visibility_value in zip(
            hourly_times,
            visibility_values
        ):
            if visibility_value is None:
                continue

            timestamp = parse_provider_datetime(
                time_value,
                provider_timezone,
            )

            if timestamp is None:
                continue

            time_difference = abs(
                (timestamp - observed_at).total_seconds()
            )

            valid_visibility.append(
                (time_difference, visibility_value)
            )

        if valid_visibility:
            closest = min(
                valid_visibility,
                key=lambda item: item[0]
            )

            if closest[0] <= 90 * 60:
                visibility_km = round(
                    closest[1] / 1000,
                    2
                )

    observation = WeatherObservation.objects.create(
        location_name=str(location),
        latitude=latitude,
        longitude=longitude,
        temperature=current.get("temperature_2m"),
        rainfall_mm=current.get("precipitation"),
        wind_speed=current.get("wind_speed_10m"),
        visibility_km=visibility_km,
        weather_condition=weather_condition,
        source="Open-Meteo (model-based current estimate)",
        observed_at=observed_at,
        expires_at=observed_at + timedelta(hours=1),
    )

    return observation
