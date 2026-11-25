
import csv

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from locations.models import Location


class Command(BaseCommand):

    help = "Import Northeast locations from the processed CSV file."

    def handle(self, *args, **options):

        base_dir = Path(__file__).resolve().parents[3]

        csv_file = (
            base_dir
            / "data"
            / "processed"
            / "northeast_final_locations.csv"
        )

        if not csv_file.exists():
            raise CommandError(
                f"CSV file not found: {csv_file}"
            )

        self.stdout.write("Reading Northeast location dataset...")

        valid_categories = {
            "city",
            "town",
            "village",
            "railway_station",
        }

        locations_to_import = []
        seen_records = set()

        invalid_records = 0
        duplicate_csv_records = 0

        with open(
            csv_file,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            required_columns = {
                "name",
                "category",
                "state",
                "latitude",
                "longitude",
            }

            if not required_columns.issubset(
                set(reader.fieldnames or [])
            ):
                raise CommandError(
                    "CSV is missing one or more required columns."
                )

            for row in reader:

                try:
                    name = row["name"].strip()
                    category = row["category"].strip()
                    state = row["state"].strip()

                    latitude = float(row["latitude"])
                    longitude = float(row["longitude"])

                    if (
                        not name
                        or not state
                        or category not in valid_categories
                        or not -90 <= latitude <= 90
                        or not -180 <= longitude <= 180
                    ):
                        invalid_records += 1
                        continue

                    record_key = (
                        name,
                        category,
                        latitude,
                        longitude,
                    )

                    if record_key in seen_records:
                        duplicate_csv_records += 1
                        continue

                    seen_records.add(record_key)

                    locations_to_import.append(
                        Location(
                            name=name,
                            category=category,
                            state=state,
                            latitude=latitude,
                            longitude=longitude,
                        )
                    )

                except (ValueError, TypeError, AttributeError):
                    invalid_records += 1

        # Find records that already exist in the database.
        existing_records = set(
            Location.objects.values_list(
                "name",
                "category",
                "latitude",
                "longitude",
            )
        )

        new_locations = [
            location
            for location in locations_to_import
            if (
                location.name,
                location.category,
                location.latitude,
                location.longitude,
            ) not in existing_records
        ]

        already_existing = (
            len(locations_to_import) - len(new_locations)
        )

        # Insert new records in one database transaction.
        with transaction.atomic():

            Location.objects.bulk_create(
                new_locations,
                batch_size=500,
                ignore_conflicts=True,
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("IMPORT COMPLETED")
        )

        self.stdout.write(
            f"Valid unique CSV records: {len(locations_to_import)}"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"New records imported: {len(new_locations)}"
            )
        )

        self.stdout.write(
            f"Already in database: {already_existing}"
        )

        self.stdout.write(
            f"Duplicate CSV records: {duplicate_csv_records}"
        )

        self.stdout.write(
            f"Invalid records skipped: {invalid_records}"
        )

        self.stdout.write(
            f"Total locations in database: {Location.objects.count()}"
        )
