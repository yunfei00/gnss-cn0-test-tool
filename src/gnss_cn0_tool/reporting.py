import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from gnss_cn0_tool.analysis import analyze
from gnss_cn0_tool.observations import observation_tables
from gnss_cn0_tool.store import Plan


def replay(folder: Path):
    plan_path = folder / "test_plan.json"
    options = {}
    if plan_path.is_file():
        plan = Plan.load(plan_path)
        options = {
            "tolerance": plan.frequency_tolerance_hz, "min_samples": plan.min_samples,
            "require_screen_off": any(
                scene.scene_id == "screen_off" for scene in plan.scenes
            ),
        }
    scenes_path = folder / "scenes.json"
    scenes = json.loads(scenes_path.read_text(encoding="utf-8")) if scenes_path.exists() else {}
    raw = folder / "raw"
    result = analyze(raw, scenes=scenes, **options)
    calibration_path = folder / "clock_calibration.json"
    if calibration_path.exists():
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        result["clock"] = [
            {"point": label, **values["best"], "uncertainty_ms": values["uncertainty_ms"],
             "quality": values["quality"]}
            for label, values in calibration.items()
        ]
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    result["raw_files"] = manifest["files"]
    result["metadata"] = [{
        "test_run_id": manifest["test_run_id"], "logger_session_id": manifest["logger_session_id"],
        "boot_count": manifest["boot_count"], "delta": "A minus B (dB)",
    }]
    extra = observation_tables(raw, manifest, scenes, options.get("tolerance", 2_000_000))
    write_reports(folder / "reports", result, extra)
    return result


def safe_csv_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_reports(folder: Path, tables, extra=()):
    folder.mkdir(exist_ok=True)
    workbook = Workbook(write_only=True)
    primary = [
        (name, list(dict.fromkeys(key for row in rows for key in row)), rows)
        for name, rows in tables.items()
    ]
    for name, keys, rows in [*primary, *extra]:
        csv_partial = folder / (name + ".csv.partial")
        sheet = workbook.create_sheet(name)
        if keys:
            sheet.append(keys)
        with csv_partial.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            if keys:
                writer.writerow(keys)
            for index, row in enumerate(rows):
                if index and index % 1_048_575 == 0:
                    sheet = workbook.create_sheet(f"{name}_{index // 1_048_575 + 1}")
                    sheet.append(keys)
                values = [row.get(key) for key in keys]
                writer.writerow(map(safe_csv_value, values))
                cells = []
                for value in values:
                    cell = WriteOnlyCell(sheet, value=value)
                    if isinstance(value, str):
                        cell.data_type = "s"
                    cells.append(cell)
                sheet.append(cells)
        csv_partial.replace(folder / (name + ".csv"))
    partial = folder / "report.partial.xlsx"
    workbook.save(partial)
    partial.replace(folder / "report.xlsx")
