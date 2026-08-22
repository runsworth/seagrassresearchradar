import unittest
import harvest

class TestRadar(unittest.TestCase):
    def test_relevance(self):
        self.assertGreaterEqual(harvest.classify("Restoration of Zostera marina", "seed planting in a seagrass meadow")["relevance_score"], 7)
    def test_ruppia_false_positive_penalty(self):
        a = harvest.classify("Ruppia in a freshwater lake", "freshwater macrophyte study")
        b = harvest.classify("Ruppia in a coastal lagoon", "seagrass ecology in an estuarine lagoon")
        self.assertGreater(b["relevance_score"], a["relevance_score"])
    def test_doi(self):
        self.assertEqual(harvest.normalize_doi("https://doi.org/10.1000/ABC.1"), "10.1000/abc.1")
    def test_publisher_aliases(self):
        self.assertEqual(harvest.infer_publisher_group("Elsevier BV"), "Elsevier")
        self.assertEqual(harvest.infer_publisher_group("Frontiers Media SA"), "Frontiers")
        self.assertEqual(harvest.infer_publisher_group("MDPI AG"), "MDPI")

if __name__ == '__main__': unittest.main()
