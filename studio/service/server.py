"""Local HTTP service for PASE Studio.

The service deliberately binds to loopback only. Network responses are cached so a
project remains useful offline and every enriched field carries provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd
import pvlib
import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "studio" / ".data"
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "cache.sqlite3"
PROJECTS = ROOT / "SIMULACOES"
PROJECTS.mkdir(exist_ok=True)
JOBS: dict[str, dict[str, Any]] = {}
PROCESSES: dict[str, subprocess.Popen] = {}


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL)"
    )
    return connection


def cached_json(url: str, max_age: int = 30 * 86400) -> tuple[Any, str]:
    key = hashlib.sha256(url.encode()).hexdigest()
    with db() as connection:
        row = connection.execute("SELECT value, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row[1] <= max_age:
            return json.loads(row[0]), "cache"
    request = urllib.request.Request(url, headers={
        "User-Agent": "PASE-Studio/1.0 (https://gitlab.uliege.be/deal-public/pase)",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
        with db() as connection:
            connection.execute("REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(value), time.time()))
        return value, "network"
    except Exception:
        if row:
            return json.loads(row[0]), "stale-cache"
        raise


def search_location(query: str) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 6})
    data, origin = cached_json(f"https://nominatim.openstreetmap.org/search?{params}")
    return [{
        "name": item["display_name"], "latitude": float(item["lat"]),
        "longitude": float(item["lon"]), "type": item.get("type"),
        "boundingBox": [float(v) for v in item.get("boundingbox", [])],
        "source": "OpenStreetMap/Nominatim", "cache": origin,
    } for item in data]


def enrich_location(lat: float, lon: float) -> dict[str, Any]:
    elevation_url = "https://api.open-meteo.com/v1/elevation?" + urllib.parse.urlencode({"latitude": lat, "longitude": lon})
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "daily": "sunrise,sunset,precipitation_sum", "forecast_days": 7,
    })
    elevation, elevation_cache = cached_json(elevation_url)
    weather, weather_cache = cached_json(weather_url, 1800)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "latitude": {"value": lat, "source": "user/geocoder", "retrievedAt": now, "confidence": 1},
        "longitude": {"value": lon, "source": "user/geocoder", "retrievedAt": now, "confidence": 1},
        "altitude": {"value": elevation["elevation"][0], "source": "Open-Meteo/Copernicus DEM", "retrievedAt": now, "cache": elevation_cache, "confidence": .85},
        "timezone": {"value": weather.get("timezone"), "source": "Open-Meteo", "retrievedAt": now, "confidence": .95},
        "weather": {"value": weather.get("current", {}), "units": weather.get("current_units", {}), "source": "Open-Meteo", "retrievedAt": now, "cache": weather_cache, "confidence": .8},
        "solarHistory": {"value": "available-on-simulation", "source": "PVGIS", "retrievedAt": now, "confidence": .9},
    }


def catalogue(kind: str, query: str, limit: int) -> list[dict[str, Any]]:
    source = "CECInverter" if kind == "inverters" else "CECMod"
    frame = pvlib.pvsystem.retrieve_sam(source)
    matches = [name for name in frame.columns if query.lower() in name.lower()][:limit]
    return [{"id": name, "name": name.replace("__", " / ").replace("_", " "),
             **{field: clean(frame[name].get(field)) for field in frame.index}}
            for name in matches]


def clean(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def solar_preview(payload: dict[str, Any]) -> dict[str, float]:
    timestamp = pd.DatetimeIndex([pd.Timestamp(payload["timestamp"], tz=payload.get("timezone", "UTC"))])
    position = pvlib.solarposition.get_solarposition(timestamp, payload["latitude"], payload["longitude"], payload.get("altitude", 0)).iloc[0]
    aoi = pvlib.irradiance.aoi(payload.get("tilt", 20), payload.get("azimuth", 180), position.apparent_zenith, position.azimuth)
    elevation = 90 - float(position.apparent_zenith)
    return {"azimuth": float(position.azimuth), "elevation": elevation, "zenith": float(position.apparent_zenith), "incidence": float(aoi)}


def optimize(payload: dict[str, Any]) -> dict[str, Any]:
    bounds = payload.get("bounds", {"width": 40, "height": 30})
    weights = payload.get("weights", {})
    panel_w = float(payload.get("panelWidth", 1.13))
    panel_h = float(payload.get("panelHeight", 2.28))
    width, height = float(bounds["width"]), float(bounds["height"])
    if min(width, height, panel_w, panel_h) <= 0:
        raise ValueError("Dimensões de área e módulo devem ser maiores que zero.")
    candidates = []
    for tilt in range(10, 51, 5):
        for azimuth in range(90, 271, 15):
            for spacing in (3, 4, 5, 6, 8, 10):
                rows = max(0, math.floor((width - panel_w) / spacing) + 1)
                per_row = max(0, math.floor((height - panel_h) / panel_h) + 1)
                count = rows * per_row
                if not count:
                    continue
                energy = count * panel_w * panel_h * max(.15, math.cos(math.radians(abs(tilt - 25)))) * max(.2, math.cos(math.radians(abs(azimuth - 180))))
                agriculture = min(1, spacing / 8) * max(.2, 1 - tilt / 120)
                access = min(1, spacing / 6)
                cost = 1 / max(1, count)
                score = (energy / max(1, width * height)) * weights.get("energy", .35) + agriculture * weights.get("agriculture", .35) + access * weights.get("access", .2) + cost * weights.get("cost", .1)
                candidates.append({"tilt": tilt, "azimuth": azimuth, "spacing": spacing, "rows": rows, "panelsPerRow": per_row, "panelCount": count, "energyIndex": round(energy, 2), "agricultureIndex": round(agriculture, 3), "score": score})
    if not candidates:
        raise ValueError("A área é menor que o módulo selecionado. Ajuste as dimensões antes de otimizar.")
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return {"best": candidates[0], "alternatives": candidates[1:6], "method": "fast-multi-objective-preview", "requiresFullValidation": True}


def set_values(path: Path, values: dict[str, Any]) -> None:
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if key in content and value is not None:
            content[key]["Value"] = value
    path.write_text(yaml.safe_dump(content, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def set_yaml_values(path: Path, values: dict[str, Any]) -> None:
    if not path.is_file() or not values:
        return
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key, value in values.items():
        if key in content and isinstance(content[key], dict) and "Value" in content[key] and value is not None:
            content[key]["Value"] = value
    path.write_text(yaml.safe_dump(content, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def input_values(path: Path) -> dict[str, Any]:
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {key: item.get("Value") for key, item in content.items()
            if isinstance(item, dict) and "Value" in item}


def input_metadata(path: Path) -> dict[str, Any]:
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    metadata = {}
    for key, item in content.items():
        if not isinstance(item, dict):
            continue
        options = item.get("Possibilities", item.get("Choices"))
        limit = item.get("Limit")
        if options is None and isinstance(limit, list) and all(isinstance(value, str) for value in limit):
            options, limit = limit, None
        metadata[key] = {
            "type": item.get("Type"),
            "options": options,
            "unit": item.get("Unit", item.get("Units")),
            "definition": item.get("Definition"),
            "limits": limit,
        }
    return metadata


def input_bundle() -> dict[str, Any]:
    inputs = ROOT / "INPUTS"
    def values(relative: str) -> dict[str, Any]:
        return input_values(inputs / relative)
    def metadata(relative: str) -> dict[str, Any]:
        return input_metadata(inputs / relative)
    crop_paths = {
        "simpleConfig": "CROPS/config/simple_example.yml", "simpleCrop": "CROPS/SIMPLE/crop_init.yaml",
        "simpleSoil": "CROPS/SIMPLE/soil_init.yaml", "grassimConfig": "CROPS/config/grassim_example.yml",
        "grassimCrop": "CROPS/GRASSIM/crop/crop_init_example.yml", "grassimSoil": "CROPS/GRASSIM/soil/soil_init_example.yml",
        "grassimManagement": "CROPS/GRASSIM/management/management_dates_example.yml",
        "grassimManagementFrequency": "CROPS/GRASSIM/management/management_frequency_example.yml",
        "grassimSoilParameters": "CROPS/GRASSIM/soil/Parameters_value_SOIL.yml",
        "grassimPftComposition": "CROPS/GRASSIM/crop/PFT_composition.yml", "grassimKcValues": "CROPS/GRASSIM/crop/Kc_values.yml",
        "sticsConfig": "CROPS/config/stics_example.yml", "stics": "CROPS/STICS/simu_init.yaml",
        "pysticsConfig": "CROPS/config/pystics_example.yml", "pystics": "CROPS/pySTICS/simu_init.yaml",
    }
    return {
        "site": values("SCENARIOS/Example1_loc.yaml"),
        "layout": values("AV_CENTRAL/Example1_AV.yaml"),
        "module": values("HARDWARE/PV_MODULES/Example1_PV_Module.yaml"),
        "structure": values("HARDWARE/STRUCTURES/agrivoltaic_fence.yaml"),
        "agriculture": {
            "simpleConfig": values("CROPS/config/simple_example.yml"),
            "simpleCrop": values("CROPS/SIMPLE/crop_init.yaml"),
            "simpleSoil": values("CROPS/SIMPLE/soil_init.yaml"),
            "grassimConfig": values("CROPS/config/grassim_example.yml"),
            "grassimCrop": values("CROPS/GRASSIM/crop/crop_init_example.yml"),
            "grassimSoil": values("CROPS/GRASSIM/soil/soil_init_example.yml"),
            "grassimManagement": values("CROPS/GRASSIM/management/management_dates_example.yml"),
            "grassimManagementFrequency": values("CROPS/GRASSIM/management/management_frequency_example.yml"),
            "grassimSoilParameters": values("CROPS/GRASSIM/soil/Parameters_value_SOIL.yml"),
            "grassimPftComposition": values("CROPS/GRASSIM/crop/PFT_composition.yml"),
            "grassimKcValues": values("CROPS/GRASSIM/crop/Kc_values.yml"),
            "sticsConfig": values("CROPS/config/stics_example.yml"),
            "stics": values("CROPS/STICS/simu_init.yaml"),
            "pysticsConfig": values("CROPS/config/pystics_example.yml"),
            "pystics": values("CROPS/pySTICS/simu_init.yaml"),
            "simpleParameters": pd.read_csv(inputs / "CROPS/SIMPLE/crops_parameters.csv").fillna("").to_dict(orient="records"),
            "grassimPftParameters": pd.read_csv(inputs / "CROPS/GRASSIM/crop/Parameters_values_PFT.csv", sep=";").fillna("").to_dict(orient="records"),
        },
        "metadata": {
            "site": metadata("SCENARIOS/Example1_loc.yaml"), "layout": metadata("AV_CENTRAL/Example1_AV.yaml"),
            "module": metadata("HARDWARE/PV_MODULES/Example1_PV_Module.yaml"),
            "structure": metadata("HARDWARE/STRUCTURES/agrivoltaic_fence.yaml"),
            "agriculture": {key: metadata(path) for key, path in crop_paths.items()},
        },
    }


def set_csv_rows(path: Path, rows: Any) -> None:
    if path.is_file() and isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
        pd.DataFrame(rows).to_csv(path, index=False, sep=";" if "GRASSIM" in str(path) else ",", lineterminator="\n")


def prepare_workspace(project: dict[str, Any], job_id: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", project.get("name", "simulation")).strip("-") or "simulation"
    workspace = PROJECTS / safe_name / "runs" / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "INPUTS", workspace / "INPUTS", dirs_exist_ok=True)
    shutil.copy2(ROOT / "example.py", workspace / "example.py")
    site, layout = project.get("site", {}), project.get("layout", {})
    set_values(workspace / "INPUTS" / "SCENARIOS" / "Example1_loc.yaml", project.get("siteParameters", {}))
    set_values(workspace / "INPUTS" / "SCENARIOS" / "Example1_loc.yaml", {
        "LocationName": safe_name, "Latitude": site.get("latitude"), "Longitude": site.get("longitude"),
        "Altitude": site.get("altitude"), "TimeZone": site.get("timezone"),
    })
    set_values(workspace / "INPUTS" / "AV_CENTRAL" / "Example1_AV.yaml", {
        "TiltY": layout.get("tilt"), "CentralAzimut": layout.get("azimuth"), "Height": layout.get("height"),
        "RepetitionDistanceOfPVBlocksX": layout.get("spacing", layout.get("blockSpacingX")),
        "RepetitionDistanceOfPVBlocksY": layout.get("blockSpacingY"),
        "NumberOfPVBlocksX": layout.get("rows", layout.get("blocksX")),
        "NumberOfPVBlocksY": layout.get("blocksY"),
        "NumberOfPanelsX": layout.get("panelsX"), "NumberOfPanelsY": layout.get("panelsPerRow", layout.get("panelsY")),
        "RepetitionDistanceOfPanelsX": layout.get("panelSpacingX"), "RepetitionDistanceOfPanelsY": layout.get("panelSpacingY"),
        "RotationAxisNumber": layout.get("rotationAxisNumber"), "Hinge": layout.get("hinge"),
    })
    module = project.get("module") or {}
    electrical_model = project.get("electricalModel", "simple")
    cec_fields = ("alpha_sc", "a_ref", "I_L_ref", "I_o_ref", "R_sh_ref", "R_s", "Adjust")
    if electrical_model == "cec":
        missing = [field for field in cec_fields if module.get(field) in (None, "")]
        if missing:
            raise ValueError("O modelo CEC exige os parâmetros do módulo: " + ", ".join(missing) + ". Escolha um módulo CEC ou preencha esses campos manualmente.")
    set_values(workspace / "INPUTS" / "HARDWARE" / "PV_MODULES" / "Example1_PV_Module.yaml", {
        "Panel_Peak_Power": module.get("STC", module.get("Panel_Peak_Power")),
        "PanelDimensionX": module.get("Length", module.get("PanelDimensionX")),
        "PanelDimensionY": module.get("Width", module.get("PanelDimensionY")),
        "PanelDimensionZ": module.get("PanelDimensionZ"),
        "Bifaciality": module.get("Bifacial", module.get("Bifaciality")),
        "Bifaciality_factor": module.get("Bifaciality_factor"),
        "PanelThickness": module.get("PanelThickness"),
    })
    set_values(workspace / "INPUTS" / "HARDWARE" / "STRUCTURES" / "agrivoltaic_fence.yaml", project.get("structure", {}))
    crop_model = project.get("agriculture", {}).get("model", "simple")
    crop_configs = {"simple": "simple_example.yml", "grassim": "grassim_example.yml", "stics": "stics_example.yml", "pystics": "pystics_example.yml"}
    selected_crop_config = crop_configs.get(crop_model, "simple_example.yml")
    set_values(workspace / "INPUTS" / "CROPS" / "config" / selected_crop_config, {"CropModel": crop_model})
    if crop_model == "simple":
        simple_config_path = workspace / "INPUTS" / "CROPS" / "config" / selected_crop_config
        simple_values = input_values(simple_config_path)
        simple_values["Option2D"] = int(project.get("agriculture", {}).get("option2D", 0))
        set_yaml_values(simple_config_path, simple_values)
    example_path = workspace / "example.py"
    source = example_path.read_text(encoding="utf-8")
    source = source.replace("import os\n", "import os\nimport numpy as np\n", 1)
    source = source.replace("file='simple_example.yml'", f"file='{selected_crop_config}'")
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "config" / "simple_example.yml", project.get("agriculture", {}).get("simpleConfig", {}))
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "SIMPLE" / "crop_init.yaml", project.get("agriculture", {}).get("simpleCrop", {}))
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "SIMPLE" / "soil_init.yaml", project.get("agriculture", {}).get("simpleSoil", {}))
    for key, relative in {
        "grassimConfig": "CROPS/config/grassim_example.yml", "grassimCrop": "CROPS/GRASSIM/crop/crop_init_example.yml",
        "grassimSoil": "CROPS/GRASSIM/soil/soil_init_example.yml", "grassimManagement": "CROPS/GRASSIM/management/management_dates_example.yml",
        "grassimManagementFrequency": "CROPS/GRASSIM/management/management_frequency_example.yml",
        "grassimSoilParameters": "CROPS/GRASSIM/soil/Parameters_value_SOIL.yml",
        "grassimPftComposition": "CROPS/GRASSIM/crop/PFT_composition.yml", "grassimKcValues": "CROPS/GRASSIM/crop/Kc_values.yml",
    }.items():
        set_yaml_values(workspace / "INPUTS" / relative, project.get("agriculture", {}).get(key, {}))
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "config" / "stics_example.yml", project.get("agriculture", {}).get("sticsConfig", {}))
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "STICS" / "simu_init.yaml", project.get("agriculture", {}).get("stics", {}))
    set_yaml_values(workspace / "INPUTS" / "CROPS" / "pySTICS" / "simu_init.yaml", project.get("agriculture", {}).get("pystics", {}))
    set_csv_rows(workspace / "INPUTS" / "CROPS" / "SIMPLE" / "crops_parameters.csv", project.get("agriculture", {}).get("simpleParameters"))
    set_csv_rows(workspace / "INPUTS" / "CROPS" / "GRASSIM" / "crop" / "Parameters_values_PFT.csv", project.get("agriculture", {}).get("grassimPftParameters"))
    # The bundled quick-start visualizes through PyVista dialogs. Studio renders its
    # own scene and consumes this display flag, so a simulation never opens a second window.
    source = source.replace("visualization=True", "visualization=False")
    source = source.replace("L.visualize_direct_light_map(1)", "# Studio visualization: direct map")
    source = source.replace("L.visualize_diffuse_light_map(10)", "# Studio visualization: diffuse map")
    source = source.replace("L.visualize_daily_irrad_map(Loc_1['SimulationStartingYear'], 15)", "# Studio visualization: daily map")
    source = source.replace("year=2005, julian_day=5", "year=Loc_1['SimulationStartingYear'], julian_day=5")
    source = source.replace("PV_params_dict = Inputs_aggregator([AV_1, PV_module_1]).aggregated_inputs",
                            "PV_params_dict = Inputs_aggregator([AV_1, PV_module_1, Structure]).aggregated_inputs")
    cec_params = {field: module[field] for field in (*cec_fields, "EgRef", "dEgdT") if field in module and module[field] is not None}
    source = source.replace("PV_central = PV_Production(PV_params_dict)",
                            f"PV_central = PV_Production(PV_params_dict, electrical_model={electrical_model!r}, module_parameters={json.dumps(cec_params)})")
    for anchor, label in {
        "# Import sun positions": "PASE Studio: calculando posições solares",
        "# Instantiation of the 3D PV central": "PASE Studio: construindo geometria dos painéis e suportes",
        "# Initiation of the object containing points of interest": "PASE Studio: preparando área de cultivo",
        "# Computation of sun and light data": "PASE Studio: preparando clima e radiação",
        "# Run light ray casting model (direct and diffuse)": "PASE Studio: calculando sombras e irradiância",
        "# PV production model": "PASE Studio: calculando produção fotovoltaica",
        "# Crop model": "PASE Studio: calculando produção agrícola",
    }.items():
        source = source.replace(anchor, f"print({label!r}, flush=True)\n{anchor}")
    source = source.replace("# Crop model", f"print('PASE_STAGE:crop_started', flush=True)\nprint('PASE Studio: modelo agrícola selecionado: {crop_model}', flush=True)\n# Crop model")
    crop_metric = ""
    if crop_model == "simple":
        crop_metric = (
            "try:\n"
            "    _year = str(Loc_1['SimulationStartingYear'])\n"
            "    _date = _year + '-10-10 00:00:00'\n"
            "    _yield = np.asarray(agro_results[_year]['Fresh_yield'][_date], dtype=float)\n"
            "    if _yield.size:\n"
            "        _mean_yield = float(np.nanmean(_yield))\n"
            "        if np.isfinite(_mean_yield):\n"
            "            print(f'PASE_METRIC:fresh_yield_10_10_g_m2={_mean_yield:.3f}', flush=True)\n"
            "except (KeyError, TypeError, ValueError):\n"
            "    pass\n"
        )
    source = source.replace("# Display spatialized dry yield",
                            "print('PASE_STAGE:crop_completed', flush=True)\nprint('PASE Studio: produção agrícola concluída', flush=True)\n" + crop_metric + "# Display spatialized dry yield")
    source = source.replace("option_2D = 1 # 0: no 2D-spatialization ; 1 : 2D spatialization",
                            "option_2D = int(crop_config.get('Option2D', 0)) # Studio crop spatialization")
    start = "# Display spatialized dry yield"
    if start in source:
        index = source.index(start)
        source = source[:index] + "# Results are presented in PASE Studio.\n"
    example_path.write_text(source, encoding="utf-8")
    (workspace / "project.pase-project").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return workspace


def crop_stage_flags(lines: list[str]) -> tuple[bool, bool]:
    """Read ASCII protocol markers independently of console code pages."""
    events = {line.strip() for line in lines if line.startswith("PASE_STAGE:")}
    return "PASE_STAGE:crop_started" in events, "PASE_STAGE:crop_completed" in events


def extract_run_results(lines: list[str], crop_model: str) -> dict[str, Any]:
    """Extract verified headline metrics without depending on localized units."""
    yearly = {}
    for line in lines:
        match = re.search(r"PV production for year (\d{4}): ([\d.]+) MW", line)
        if match:
            yearly[match.group(1)] = float(match.group(2))
    _, crop_finished = crop_stage_flags(lines)
    result = {"pvMWhByYear": yearly, "cropModel": crop_model, "cropCompleted": crop_finished}
    for line in lines:
        match = re.match(r"PASE_METRIC:fresh_yield_10_10_g_m2=([0-9]+(?:\.[0-9]+)?)", line)
        if match:
            result["freshYieldOctoberGm2"] = float(match.group(1))
    return result


def request_cancel(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise ValueError("Simulação não encontrada.")
    if job["status"] not in ("queued", "running", "cancelling"):
        return job
    job["cancelRequested"] = True
    job["status"] = "cancelling"
    process = PROCESSES.get(job_id)
    if process and process.poll() is None:
        process.terminate()
    return job


def run_job(job_id: str, project: dict[str, Any]) -> None:
    job = JOBS[job_id]
    if job.get("cancelRequested"):
        job.update(status="cancelled", progress=100, finishedAt=time.time())
        return
    job.update(status="running", progress=5, startedAt=time.time())
    try:
        workspace = prepare_workspace(project, job_id)
        if job.get("cancelRequested"):
            job.update(status="cancelled", progress=100, finishedAt=time.time(), workspace=str(workspace))
            return
        environment = {**os.environ, "PYTHONPATH": str(ROOT), "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PASE_SAFE_RAYCAST": "1", "PASE_STUDIO": "1"}
        process = subprocess.Popen([sys.executable, "-u", "example.py"], cwd=workspace, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        PROCESSES[job_id] = process
        job["pid"] = process.pid
        if job.get("cancelRequested"):
            process.terminate()
        assert process.stdout
        with (workspace / "studio-run.log").open("w", encoding="utf-8") as output:
            for line in process.stdout:
                clean_line = line.rstrip()
                output.write(line)
                output.flush()
                job["log"].append(clean_line)
                if clean_line.startswith("PASE Studio:"):
                    job["lastStage"] = clean_line
                job["progress"] = min(95, job["progress"] + 1)
        code = process.wait()
        log_path = workspace / "logging_file.log"
        if log_path.exists():
            job["log"].extend(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
        crop_started, crop_finished = crop_stage_flags(job["log"])
        job["results"] = extract_run_results(job["log"], project.get("agriculture", {}).get("model", "simple"))
        complete = code == 0 and crop_finished
        cancelled = bool(job.get("cancelRequested"))
        job.update(status="cancelled" if cancelled else "completed" if complete else "failed", progress=100, exitCode=code, finishedAt=time.time(), workspace=str(workspace))
        if not complete and not cancelled:
            detail = next((line for line in reversed(job["log"]) if "ERROR" in line or re.match(r"(?:\w+Error|Exception):", line)), None)
            last_stage = job.get("lastStage", "inicialização")
            if code == 0 and crop_started and not crop_finished:
                job["error"] = detail or f"A etapa agrícola não terminou. Último estágio: {last_stage}."
            elif code == 0 and not crop_started:
                job["error"] = detail or f"A execução terminou antes da etapa agrícola. Último estágio: {last_stage}. Consulte a saída detalhada abaixo."
            else:
                job["error"] = detail or f"O motor encerrou no estágio {last_stage} (código Windows 0x{code & 0xFFFFFFFF:08X}). Consulte a saída detalhada abaixo."
    except Exception as exc:
        if job.get("cancelRequested"):
            job.update(status="cancelled", progress=100, finishedAt=time.time())
        else:
            job.update(status="failed", progress=100, error=f"{type(exc).__name__}: {exc}", finishedAt=time.time())
    finally:
        PROCESSES.pop(job_id, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def send(self, value: Any, status: int = 200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send({})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                return self.send({"status": "ok", "version": 1})
            if parsed.path == "/studio/config":
                return self.send(input_bundle())
            if parsed.path in ("/catalog/modules", "/catalog/inverters"):
                kind = parsed.path.rsplit("/", 1)[1]
                return self.send({"items": catalogue(kind, query.get("q", [""])[0], int(query.get("limit", ["40"])[0]))})
            if parsed.path.startswith("/jobs/"):
                job = JOBS.get(parsed.path.rsplit("/", 1)[1], {"status": "missing"})
                return self.send({**job, "log": job.get("log", [])[-300:]})
            self.send({"error": "not found"}, 404)
        except Exception as exc:
            self.send({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if self.path == "/locations/search":
                return self.send({"items": search_location(payload["query"])})
            if self.path == "/locations/enrich":
                return self.send(enrich_location(float(payload["latitude"]), float(payload["longitude"])))
            if self.path == "/preview/solar":
                return self.send(solar_preview(payload))
            if self.path == "/layout/optimize":
                return self.send(optimize(payload))
            if self.path == "/simulation/start":
                job_id = uuid.uuid4().hex
                JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0, "log": []}
                project = payload.get("project", {})
                threading.Thread(target=run_job, args=(job_id, project), daemon=True).start()
                return self.send(JOBS[job_id], 202)
            if self.path == "/simulation/cancel":
                return self.send(request_cancel(payload["id"]))
            self.send({"error": "not found"}, 404)
        except Exception as exc:
            self.send({"error": str(exc)}, 400)


if __name__ == "__main__":
    port = int(os.environ.get("PASE_STUDIO_PORT", "8765"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
