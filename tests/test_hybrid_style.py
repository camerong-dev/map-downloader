import unittest

from hybrid_style import road_group, DEFAULT_ROADS, osm_display_scale, osm_style


class RoadDisplayTests(unittest.TestCase):
    def test_theme_validation_and_isolation(self):
        light, light_roads = osm_style()
        dark, dark_roads = osm_style('Dark')
        self.assertEqual(set(light), set(dark))
        self.assertEqual(set(light_roads), set(dark_roads))
        self.assertNotEqual(light['land'], dark['land'])
        self.assertIs(osm_style()[0], light)
        with self.assertRaises(ValueError):
            osm_style('Unknown')

    def test_osm_size_range(self):
        for value in (1, 1.5, 2, 3, 4):
            self.assertEqual(osm_display_scale(value), value)
        for value in (0, 0.5, 4.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                osm_display_scale(value)

    def test_numbered_roads_and_classification_fallback(self):
        examples = [({'highway': 'motorway_link'}, 'motorway'),
                    ({'highway': 'primary', 'ref': 'M23'}, 'motorway'),
                    ({'highway': 'tertiary', 'ref': 'A27'}, 'a_road'),
                    ({'highway': 'unclassified', 'ref': 'B 2110'}, 'b_road'),
                    ({'highway': 'secondary', 'ref': ' B2110; A272'}, 'a_road'),
                    ({'highway': 'trunk_link'}, 'a_road'),
                    ({'highway': 'secondary'}, 'b_road'),
                    ({'highway': 'tertiary_link'}, 'main'),
                    ({'highway': 'residential'}, 'local'),
                    ({'highway': 'service'}, 'local'),
                    ({'highway': 'unclassified'}, 'local')]
        for tags, expected in examples:
            with self.subTest(tags=tags):
                self.assertEqual(road_group(tags), expected)
        self.assertNotIn('local', DEFAULT_ROADS)
