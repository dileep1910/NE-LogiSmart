
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class WeatherObservation(models.Model):
    """
    Stores weather observations collected from external providers.
    """

    location_name = models.CharField(max_length=200)

    latitude = models.FloatField()
    longitude = models.FloatField()

    temperature = models.FloatField(null=True, blank=True)
    rainfall_mm = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)]
    )
    wind_speed = models.FloatField(null=True, blank=True)
    visibility_km = models.FloatField(null=True, blank=True)

    weather_condition = models.CharField(max_length=100, blank=True)

    source = models.CharField(max_length=150)

    observed_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at"]
        indexes = [
            models.Index(fields=["location_name", "observed_at"]),
        ]

    def __str__(self):
        return f"{self.location_name} - {self.weather_condition}"


class HazardObservation(models.Model):
    """
    Stores natural hazards and road disruption observations.
    """

    class HazardType(models.TextChoices):
        LANDSLIDE = "landslide", "Landslide"
        FLOOD = "flood", "Flood"
        ACCIDENT = "accident", "Accident"
        ROAD_DAMAGE = "road_damage", "Road Damage"
        HEAVY_RAIN = "heavy_rain", "Heavy Rain"
        STORM = "storm", "Storm"
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        ACTIVE = "active", "Active"
        RESOLVED = "resolved", "Resolved"

    title = models.CharField(max_length=200)

    hazard_type = models.CharField(
        max_length=30,
        choices=HazardType.choices
    )

    description = models.TextField(blank=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    state = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)

    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MODERATE
    )

    source = models.CharField(max_length=200, blank=True)

    confidence = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNVERIFIED
    )

    observed_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-observed_at"]
        indexes = [
            models.Index(fields=["hazard_type", "status"]),
            models.Index(fields=["state", "district"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_severity_display()})"


class RouteRiskAssessment(models.Model):
    """
    Stores calculated risk assessments for individual routes.
    """

    source_location = models.CharField(max_length=200)
    destination_location = models.CharField(max_length=200)

    risk_score = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )

    risk_level = models.CharField(
        max_length=30,
        default="insufficient_data"
    )

    risk_factors = models.JSONField(default=dict, blank=True)

    data_completeness = models.FloatField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )

    assessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-assessed_at"]
        indexes = [
            models.Index(fields=["assessed_at"]),
        ]

    def __str__(self):
        return (
            f"{self.source_location} to "
            f"{self.destination_location} - {self.risk_level}"
        )


class HistoricalLandslide(models.Model):
    """
    Stores historical landslide inventory records imported
    from Geological Survey of India (GSI) Excel datasets.

    These records represent historical inventory locations,
    NOT automatically active landslide incidents.
    """

    slide_number = models.CharField(
        max_length=150,
        blank=True
    )

    state = models.CharField(max_length=100)
    district = models.CharField(max_length=150, blank=True)

    toposheet_number = models.CharField(
        max_length=100,
        blank=True
    )

    slide_name = models.CharField(
        max_length=255,
        blank=True
    )

    road_location = models.TextField(blank=True)

    latitude = models.FloatField(
        validators=[
            MinValueValidator(-90),
            MaxValueValidator(90)
        ]
    )

    longitude = models.FloatField(
        validators=[
            MinValueValidator(-180),
            MaxValueValidator(180)
        ]
    )

    material_involved = models.CharField(
        max_length=255,
        blank=True
    )

    movement_type = models.CharField(
        max_length=100,
        blank=True
    )

    # Preserve the original GSI history value without
    # assuming that it is always a complete date.
    history_text = models.TextField(blank=True)

    source = models.CharField(
        max_length=150,
        default="Geological Survey of India (GSI)"
    )

    source_file = models.CharField(max_length=255)

    # Original Excel row number, useful for traceability.
    source_row_number = models.PositiveIntegerField()

    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["state", "district", "slide_number"]

        constraints = [
            models.UniqueConstraint(
                fields=["source_file", "source_row_number"],
                name="unique_historical_landslide_source_row"
            )
        ]

        indexes = [
            models.Index(fields=["state", "district"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return (
            f"{self.slide_number or 'Unknown slide'} - "
            f"{self.district}, {self.state}"
        )
