import unittest

from hybrid_style import road_group, DEFAULT_ROADS


class RoadDisplayTests(unittest.TestCase):
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
