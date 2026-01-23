import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from frontend import main


class SummarySdmxTests(unittest.TestCase):
    def get_dataset_by_structure_name(self, message, name):
        structures = message["data"]["structures"]
        target_index = next(index for index, item in enumerate(structures) if item["name"] == name)
        return next(
            dataset for dataset in message["data"]["dataSets"] if dataset["structure"] == target_index
        )

    def test_true_count_extraction(self):
        self.assertEqual(main.get_true_count({"true": 5, "false": 1}), 5)
        self.assertEqual(main.get_true_count({"True": 7}), 7)
        self.assertEqual(main.get_true_count({"FALSE": 2}), 0)
        self.assertEqual(main.get_true_count({}), 0)
        self.assertEqual(main.get_true_count(None), 0)

    def test_service_stats_values_and_pages(self):
        solr_response = {
            "response": {"numFound": 123},
            "facet_counts": {
                "facet_fields": {
                    "facet-itemLevel": ["false", 10, "TRUE", 2],
                    "facet-pageHasTranscription": ["true", 5],
                    "facet-pageHasTranslation": ["False", 7, "true", 3],
                    "facet-hasImage": ["TRUE", 9],
                    "facet-collection": ["Collection A", 4],
                }
            },
        }
        message = main.build_sdmx_summary(solr_response)
        service_obs = self.get_dataset_by_structure_name(message, "service_stats")["observations"]

        self.assertEqual(service_obs["0"][0], 123)
        self.assertEqual(service_obs["1"][0], 2)
        self.assertEqual(service_obs["2"][0], 5)
        self.assertEqual(service_obs["3"][0], 3)
        self.assertEqual(service_obs["4"][0], 9)

    def test_facet_datasets_split(self):
        solr_response = {
            "response": {"numFound": 1},
            "facet_counts": {
                "facet_fields": {
                    "facet-collection": ["Collection A", 4, "Collection B", 2],
                }
            },
        }
        message = main.build_sdmx_summary(solr_response)
        dataset = self.get_dataset_by_structure_name(message, "facet-collection")
        values = message["data"]["structures"][dataset["structure"]]["dimensions"]["observation"][0]["values"]

        self.assertEqual(values[0]["name"], "Collection A")
        self.assertEqual(dataset["observations"]["0"][0], 4)
        self.assertEqual(values[1]["name"], "Collection B")
        self.assertEqual(dataset["observations"]["1"][0], 2)

    def test_excluded_facets_not_returned(self):
        solr_response = {
            "response": {"numFound": 1},
            "facet_counts": {
                "facet_fields": {
                    "facet-itemLevel": ["true", 1],
                    "facet-hasPage": ["true", 1],
                    "facet-pageHasTranscription": ["true", 1],
                }
            },
        }
        message = main.build_sdmx_summary(solr_response)
        structure_names = [structure["name"] for structure in message["data"]["structures"]]
        self.assertNotIn("facet-itemLevel", structure_names)
        self.assertNotIn("facet-hasPage", structure_names)
        self.assertIn("facet-pageHasTranscription", structure_names)


if __name__ == "__main__":
    unittest.main()
