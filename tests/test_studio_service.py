"""Regression tests for Studio's simulation hand-off."""

import sys
from pathlib import Path

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio" / "service"))
import server  # noqa: E402


def test_crop_stage_flags_ignore_windows_console_encoding():
    lines = [
        "PASE Studio: produ��o agr�cola conclu�da",
        "PASE_STAGE:crop_started",
        "PASE_STAGE:crop_completed",
    ]
    assert server.crop_stage_flags(lines) == (True, True)


def test_prepared_simulation_uses_explicit_ascii_completion_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path)
    project = {
        "name": "studio-test",
        "site": {"latitude": 50.565114, "longitude": 4.702089},
        "layout": {"rows": 3, "panelsPerRow": 40, "spacing": 8, "blocksY": 1},
        "agriculture": {"model": "simple"},
    }
    workspace = server.prepare_workspace(project, "run")
    source = (workspace / "example.py").read_text(encoding="utf-8")
    compile(source, str(workspace / "example.py"), "exec")
    assert "PASE_STAGE:crop_started" in source
    assert "PASE_STAGE:crop_completed" in source
    assert "PASE_METRIC:fresh_yield_10_10_g_m2" in source
    layout = yaml.safe_load((workspace / "INPUTS" / "AV_CENTRAL" / "Example1_AV.yaml").read_text(encoding="utf-8"))
    assert layout["NumberOfPVBlocksX"]["Value"] == 3
    assert layout["NumberOfPanelsY"]["Value"] == 40
    assert layout["RepetitionDistanceOfPVBlocksX"]["Value"] == 8


def test_chosen_site_is_not_overwritten_by_stale_manual_location(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path)
    project = {
        "name": "site-test",
        "site": {"latitude": -21.7546, "longitude": -41.3242, "altitude": 14, "timezone": "America/Sao_Paulo"},
        "siteParameters": {"Latitude": 50.565114, "Longitude": 4.702089},
        "layout": {},
        "agriculture": {"model": "simple"},
    }
    workspace = server.prepare_workspace(project, "run")
    site = yaml.safe_load((workspace / "INPUTS" / "SCENARIOS" / "Example1_loc.yaml").read_text(encoding="utf-8"))
    assert site["Latitude"]["Value"] == -21.7546
    assert site["Longitude"]["Value"] == -41.3242


def test_optimizer_uses_actual_width_for_block_spacing_and_height_for_panels():
    result = server.optimize({
        "bounds": {"width": 24, "height": 10},
        "panelWidth": 2,
        "panelHeight": 1,
        "weights": {"energy": 1, "agriculture": 0, "access": 0, "cost": 0},
    })
    best = result["best"]
    assert best["rows"] == 8
    assert best["panelsPerRow"] == 10


def test_result_summary_survives_mojibake_in_energy_unit():
    summary = server.extract_run_results([
        "PV production for year 2020: 34.35 MW�h",
        "PASE_STAGE:crop_completed",
        "PASE_METRIC:fresh_yield_10_10_g_m2=815.234",
    ], "simple")
    assert summary == {"pvMWhByYear": {"2020": 34.35}, "cropModel": "simple", "cropCompleted": True, "freshYieldOctoberGm2": 815.234}


def test_running_job_can_be_cancelled_without_being_reported_as_failure(monkeypatch):
    class FakeProcess:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    process = FakeProcess()
    monkeypatch.setattr(server, "JOBS", {"one": {"id": "one", "status": "running", "progress": 25, "log": []}})
    monkeypatch.setattr(server, "PROCESSES", {"one": process})
    response = server.request_cancel("one")
    assert response["status"] == "cancelling"
    assert server.JOBS["one"]["cancelRequested"] is True
    assert process.terminated


def test_location_search_and_enrichment_with_mocked_open_data(monkeypatch):
    def cached(url, max_age=0):
        if "nominatim" in url:
            return [{"display_name": "Test Farm", "lat": "-21.75", "lon": "-41.32", "boundingbox": ["-21.8", "-21.7", "-41.4", "-41.3"]}], "cache"
        if "elevation" in url:
            return {"elevation": [14]}, "network"
        return {"timezone": "America/Sao_Paulo", "current": {"temperature_2m": 24}, "current_units": {"temperature_2m": "°C"}}, "network"

    monkeypatch.setattr(server, "cached_json", cached)
    result = server.search_location("Test Farm")
    assert result[0]["latitude"] == -21.75
    enriched = server.enrich_location(result[0]["latitude"], result[0]["longitude"])
    assert enriched["altitude"]["value"] == 14
    assert enriched["timezone"]["value"] == "America/Sao_Paulo"
    assert enriched["weather"]["value"]["temperature_2m"] == 24


def test_solar_preview_uses_location_time_zone():
    preview = server.solar_preview({
        "timestamp": "2026-09-29T12:00:00",
        "timezone": "Europe/Brussels",
        "latitude": 50.565114,
        "longitude": 4.702089,
        "altitude": 165,
        "tilt": 20,
        "azimuth": 180,
    })
    assert 0 < preview["elevation"] < 90
    assert 0 <= preview["azimuth"] < 360
