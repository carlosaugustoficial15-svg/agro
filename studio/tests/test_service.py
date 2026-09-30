import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "pase_studio_service", Path(__file__).parents[1] / "service" / "server.py"
)
service = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(service)


class StudioServiceTests(unittest.TestCase):
    def test_optimizer_returns_feasible_ranked_layout(self):
        result = service.optimize({
            "bounds": {"width": 40, "height": 30},
            "weights": {"energy": .35, "agriculture": .35, "access": .2, "cost": .1},
        })
        best = result["best"]
        self.assertGreater(best["panelCount"], 0)
        self.assertLessEqual(best["rows"] * best["spacing"], 40)
        self.assertTrue(all(best["score"] >= item["score"] for item in result["alternatives"]))

    def test_solar_preview_has_physical_angles(self):
        result = service.solar_preview({
            "latitude": -23.55, "longitude": -46.63, "altitude": 760,
            "timezone": "America/Sao_Paulo", "timestamp": "2026-09-29T12:00:00",
            "tilt": 20, "azimuth": 180,
        })
        self.assertGreaterEqual(result["azimuth"], 0)
        self.assertLessEqual(result["azimuth"], 360)
        self.assertGreaterEqual(result["incidence"], 0)
        self.assertLessEqual(result["incidence"], 180)

    def test_catalog_search_returns_real_cec_modules(self):
        items = service.catalogue("modules", "Canadian", 3)
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item.get("STC") for item in items))


if __name__ == "__main__":
    unittest.main()
