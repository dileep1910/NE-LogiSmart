
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from openpyxl import load_workbook

from accessibility.models import HistoricalLandslide


class Command(BaseCommand):
    help = "Import historical landslide inventory from GSI Excel files."

    DATA_DIRECTORY = (
        Path(settings.BASE_DIR)
        / "data"
        / "external_datasets"
        / "landslides"
    )

    HEADER_ROW = 5
    FIRST_DATA_ROW = 6

    STATE_FILES = [
        "arunachal_pradesh.xlsx",
        "assam.xlsx",
        "manipur.xlsx",
        "meghalaya.xlsx",
        "mizoram.xlsx",
        "nagaland.xlsx",
        "sikkim.xlsx",
        "tripura.xlsx",
    ]

    NULL_VALUES = {"", "NULL", "NA", "N/A", "NONE", "NAN"}

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate files without saving database records.",
        )

    def clean_text(self, value):
        if value is None:
            return ""

        text = str(value).strip()

        if text.upper() in self.NULL_VALUES:
            return ""

        return text

    def coordinate(self, value, minimum, maximum):
        if value is None or isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if not minimum <= number <= maximum:
            return None

        return number

    def filename_to_state(self, filename):
        state = Path(filename).stem
        return state.replace("_", " ").title()

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not self.DATA_DIRECTORY.exists():
            raise CommandError(
                f"Dataset directory not found: {self.DATA_DIRECTORY}"
            )

        missing_files = [
            filename
            for filename in self.STATE_FILES
            if not (self.DATA_DIRECTORY / filename).is_file()
        ]

        if missing_files:
            raise CommandError(
                "Missing GSI Excel files: "
                + ", ".join(missing_files)
            )

        totals = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        }

        self.stdout.write(
            self.style.SUCCESS(
                "Starting GSI historical landslide import..."
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: No database records will be saved."
                )
            )

        for filename in self.STATE_FILES:
            file_path = self.DATA_DIRECTORY / filename

            self.stdout.write(f"\nProcessing: {filename}")

            try:
                workbook = load_workbook(
                    file_path,
                    read_only=True,
                    data_only=True,
                )

                if "Landslide Inventory" not in workbook.sheetnames:
                    workbook.close()
                    self.stderr.write(
                        self.style.ERROR(
                            f"Missing sheet in {filename}"
                        )
                    )
                    totals["failed"] += 1
                    continue

                sheet = workbook["Landslide Inventory"]

                headers = [
                    self.clean_text(cell.value).upper()
                    for cell in sheet[self.HEADER_ROW]
                ]

                column_map = {
                    header: index
                    for index, header in enumerate(headers)
                    if header
                }

                required_columns = {
                    "STATE",
                    "DISTRICT",
                    "LATITUDE",
                    "LONGITUDE",
                }

                missing_columns = (
                    required_columns - set(column_map)
                )

                if missing_columns:
                    workbook.close()
                    self.stderr.write(
                        self.style.ERROR(
                            "Missing columns in "
                            f"{filename}: {', '.join(sorted(missing_columns))}"
                        )
                    )
                    totals["failed"] += 1
                    continue

                file_created = 0
                file_updated = 0
                file_skipped = 0
                file_failed = 0

                for row_number, row in enumerate(
                    sheet.iter_rows(
                        min_row=self.FIRST_DATA_ROW,
                        values_only=True,
                    ),
                    start=self.FIRST_DATA_ROW,
                ):
                    def get_value(column):
                        index = column_map.get(column)

                        if index is None or index >= len(row):
                            return None

                        return row[index]

                    # Ignore completely empty rows.
                    if not any(value is not None for value in row):
                        continue

                    latitude = self.coordinate(
                        get_value("LATITUDE"), -90, 90
                    )

                    longitude = self.coordinate(
                        get_value("LONGITUDE"), -180, 180
                    )

                    if latitude is None or longitude is None:
                        file_skipped += 1
                        continue

                    state = self.filename_to_state(filename)

                    district = self.clean_text(
                        get_value("DISTRICT")
                    )

                    defaults = {
                        "slide_number": self.clean_text(
                            get_value("SLIDE NO")
                        ),
                        "state": state,
                        "district": district,
                        "toposheet_number": self.clean_text(
                            get_value("TOPOSHEET NO")
                        ),
                        "slide_name": self.clean_text(
                            get_value("SLIDE NAME")
                        ),
                        "road_location": self.clean_text(
                            get_value("NH/SH LOCATION")
                        ),
                        "latitude": latitude,
                        "longitude": longitude,
                        "material_involved": self.clean_text(
                            get_value("MATERIAL INVOLVED")
                        ),
                        "movement_type": self.clean_text(
                            get_value("MOVEMENT TYPE")
                        ),
                        "history_text": self.clean_text(
                            get_value("HISTORY")
                        ),
                        "source": (
                            "Geological Survey of India (GSI)"
                        ),
                    }

                    if dry_run:
                        file_created += 1
                        continue

                    try:
                        _, created = (
                            HistoricalLandslide.objects.update_or_create(
                                source_file=filename,
                                source_row_number=row_number,
                                defaults=defaults,
                            )
                        )

                        if created:
                            file_created += 1
                        else:
                            file_updated += 1

                    except Exception as error:
                        file_failed += 1

                        self.stderr.write(
                            self.style.ERROR(
                                f"Row {row_number} in {filename}: "
                                f"{error}"
                            )
                        )

                workbook.close()

                totals["created"] += file_created
                totals["updated"] += file_updated
                totals["skipped"] += file_skipped
                totals["failed"] += file_failed

                self.stdout.write(
                    f"Created: {file_created} | "
                    f"Updated: {file_updated} | "
                    f"Skipped: {file_skipped} | "
                    f"Failed: {file_failed}"
                )

            except Exception as error:
                totals["failed"] += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"Could not process {filename}: {error}"
                    )
                )

        self.stdout.write("\n========== IMPORT SUMMARY ==========")

        self.stdout.write(f"Created: {totals['created']}")
        self.stdout.write(f"Updated: {totals['updated']}")
        self.stdout.write(f"Skipped: {totals['skipped']}")
        self.stdout.write(f"Failed: {totals['failed']}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run completed. No database changes made."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "GSI landslide import completed."
                )
            )
