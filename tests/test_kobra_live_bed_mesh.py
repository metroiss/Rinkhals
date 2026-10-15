import copy
import json
import os
import sys
import tempfile
import types
import unittest

from tests.test_kobra import (
    DummyWebRequest,
    KLIPPY_CONNECTION_NAME,
    load_kobra_module,
)


SAVED = {
    "bed_mesh default": {
        "algo": "lagrange", "max_x": "245", "max_y": "245", "min_x": "5", "min_y": "5",
        "mesh_x_pps": "2", "mesh_y_pps": "2", "tension": "0.2", "version": "1",
        "x_count": "3", "y_count": "3",
        "points": "0.1, 0.2, 0.3\n0.4, 0.5, 0.6\n0.7, 0.8, 0.9",
    }
}

LIVE = {
    "profile_name": "",
    "mesh_min": [58.9, 58.0],
    "mesh_max": [190.5, 189.6],
    "probed_matrix": [[-0.1, 0.0], [0.05, 0.06]],
    "mesh_matrix": [[-0.1, 0.0], [0.05, 0.06]],
    "profiles": {},
}

CLEARED = {
    "profile_name": "",
    "mesh_min": [0.0, 0.0],
    "mesh_max": [0.0, 0.0],
    "probed_matrix": [[]],
    "mesh_matrix": [[]],
    "profiles": {},
}


def build_connection_module(live_status=None, reject_bed_mesh=False):
    module = types.ModuleType(KLIPPY_CONNECTION_NAME)

    class KlippyConnection:
        def __init__(self):
            self.sent = []

        async def request(self, web_request):
            return None

        async def _request_standard(self, web_request, timeout=None):
            args = copy.deepcopy(web_request.get_args())
            self.sent.append(args)
            objects = args.get("objects", {})
            if reject_bed_mesh and "bed_mesh" in objects:
                raise RuntimeError("unknown object bed_mesh")
            status = {}
            for name in objects:
                if name == "bed_mesh":
                    status["bed_mesh"] = copy.deepcopy(live_status) if live_status is not None else {}
                else:
                    status[name] = {"ok": True}
            return {"eventtime": 1.0, "status": status}

    module.KlippyConnection = KlippyConnection
    sys.modules[KLIPPY_CONNECTION_NAME] = module
    return module


class LiveBedMeshTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_kobra_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path = os.path.join(self.tmp.name, "printer_mutable.cfg")
        with open(self.saved_path, "w") as f:
            json.dump(SAVED, f)
        self.module.PRINTER_MUTABLE_CFG = self.saved_path
        self.module.read_saved_bed_mesh.__defaults__ = (self.saved_path,)

    def tearDown(self):
        self.tmp.cleanup()

    async def _query(self, live_status, objects, reject=False):
        conn_module = build_connection_module(live_status, reject)
        kobra = self.module.Kobra.__new__(self.module.Kobra)
        kobra.is_goklipper_running = lambda: True
        kobra.patch_bed_mesh()
        conn = conn_module.KlippyConnection()
        req = DummyWebRequest("objects/subscribe", {"objects": objects})
        result = await conn._request_standard(req)
        return result, conn, kobra

    async def test_adaptive_mesh_from_gklib_is_shown(self):
        result, conn, _ = await self._query(LIVE, {"bed_mesh": None, "bed_mesh default": None, "toolhead": None})
        self.assertIn("bed_mesh", conn.sent[0]["objects"])
        self.assertNotIn("bed_mesh default", conn.sent[0]["objects"])
        mesh = result["status"]["bed_mesh"]
        self.assertEqual(mesh["probed_matrix"], LIVE["probed_matrix"])
        self.assertEqual(list(mesh["mesh_min"]), LIVE["mesh_min"])
        self.assertEqual(mesh["profile_name"], "adaptive")
        self.assertEqual(result["status"]["bed_mesh default"]["mesh_params"]["x_count"], 3)
        self.assertEqual(result["status"]["toolhead"], {"ok": True})

    async def test_no_live_mesh_falls_back_to_saved(self):
        result, _, _ = await self._query(CLEARED, {"bed_mesh": None})
        mesh = result["status"]["bed_mesh"]
        self.assertEqual(mesh["profile_name"], "default")
        self.assertEqual(mesh["probed_matrix"][2], [0.7, 0.8, 0.9])

    async def test_gklib_rejecting_bed_mesh_retries_without_it(self):
        result, conn, kobra = await self._query(LIVE, {"bed_mesh": None, "toolhead": None}, reject=True)
        self.assertEqual(len(conn.sent), 2)
        self.assertNotIn("bed_mesh", conn.sent[1]["objects"])
        self.assertEqual(result["status"]["bed_mesh"]["profile_name"], "default")
        self.assertFalse(kobra.live_bed_mesh)

    async def test_profile_only_query_does_not_ask_gklib(self):
        result, conn, _ = await self._query(LIVE, {"bed_mesh default": None})
        self.assertEqual(conn.sent[0]["objects"], {})
        self.assertEqual(result["status"]["bed_mesh"]["profile_name"], "default")

    def test_is_live_bed_mesh(self):
        f = self.module.is_live_bed_mesh
        self.assertTrue(f(LIVE))
        self.assertFalse(f(CLEARED))
        self.assertFalse(f({}))
        self.assertFalse(f(None))
        self.assertFalse(f({"probed_matrix": [[1.0]], "mesh_min": None, "mesh_max": [1, 1]}))


if __name__ == "__main__":
    unittest.main()
