import unittest
from downloader import Bounds
from map_plan import MapPlan, geographic_plan, projected_plan


class MapPlanTests(unittest.TestCase):
    def test_known_israel_area_and_resolution_cost(self):
        bounds = Bounds(34,30.999954392933986,34.85715913091119,31.86735419106541)
        detailed = geographic_plan(bounds, 2)
        coarse = geographic_plan(bounds, 10)
        self.assertEqual((detailed.width, detailed.height, detailed.blocks), (47710,56585,168))
        self.assertEqual(coarse.blocks, 9)
        self.assertGreater(detailed.raw_bytes, coarse.raw_bytes * 24)
        self.assertIn('GiB before compression', detailed.summary())

    def test_pixel_alignment_and_partial_blocks(self):
        plan = projected_plan(4096.1, 8192, 1)
        self.assertEqual((plan.width,plan.height,plan.columns,plan.rows,plan.blocks), (4097,8192,2,2,4))
        self.assertEqual(plan.raw_bytes,4097*8192*4)

    def test_invalid_and_unrepresentable_exports_are_rejected(self):
        for resolution in (0,-1,float('nan'),float('inf'),1e-20,1e-320):
            with self.subTest(resolution=resolution), self.assertRaises(ValueError):
                geographic_plan(Bounds(0,50,1,51), resolution)
        with self.assertRaises(ValueError):
            geographic_plan(Bounds(0,86,1,87),2)
        with self.assertRaises(ValueError):
            MapPlan(100,100,63)
