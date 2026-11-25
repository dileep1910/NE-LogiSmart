
from django.db import models


class Location(models.Model):

    CATEGORY_CHOICES = [
        ("city", "City"),
        ("town", "Town"),
        ("village", "Village"),
        ("railway_station", "Railway Station"),
    ]

    name = models.CharField(max_length=200)

    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES
    )

    state = models.CharField(max_length=100)

    latitude = models.FloatField()

    longitude = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["state", "name"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "name",
                    "category",
                    "latitude",
                    "longitude",
                ],
                name="unique_location_record",
            )
        ]

    def __str__(self):
        return f"{self.name}, {self.state}"
