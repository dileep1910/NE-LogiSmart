
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from locations.models import Location


class Route(models.Model):

    TRANSPORT_CHOICES = [
        ("Road", "Road"),
        ("Rail", "Rail"),
        ("Air", "Air"),
    ]

    source = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="outgoing_routes",
        null=True,
        blank=True,
    )

    destination = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="incoming_routes",
        null=True,
        blank=True,
    )

    transport_type = models.CharField(
        max_length=20,
        choices=TRANSPORT_CHOICES,
    )

    distance_km = models.FloatField(
        validators=[MinValueValidator(0.1)]
    )

    estimated_time_hours = models.FloatField(
        validators=[MinValueValidator(0.1)]
    )

    estimated_cost = models.FloatField(
        validators=[MinValueValidator(0)]
    )

    reliability_score = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Reliability score from 1 to 100",
    )

    accessibility_score = models.PositiveIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Accessibility score from 1 to 100",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"{self.source} to {self.destination} "
            f"({self.transport_type})"
        )
