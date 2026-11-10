import unittest
from unittest.mock import patch

from catalogue import dependencies


class DependencyCacheTests(unittest.TestCase):
    def setUp(self):
        conda = patch.dict(dependencies._CONDA_CACHE, clear=True)
        upstream = patch.dict(dependencies._UPSTREAM_CACHE, clear=True)
        conda.start()
        upstream.start()
        self.addCleanup(conda.stop)
        self.addCleanup(upstream.stop)

    def test_conda_reuses_lookups_but_returns_independent_results(self):
        info = {**dependencies._empty_conda_info("example", "bioconda"), "latest_version": "2.0"}
        with patch.object(dependencies, "check_conda_package", return_value=info) as lookup:
            first = dependencies._conda_info("example", "1.0", preferred_channel="bioconda")
            first["latest_version"] = "changed"
            second = dependencies._conda_info("example", "1.0", preferred_channel="bioconda")
            self.assertEqual(second["latest_version"], "2.0")
            lookup.assert_called_once_with(
                "example", "1.0", channels=("bioconda", "conda-forge", "defaults"),
            )
            dependencies._conda_info("example", "1.1", preferred_channel="bioconda")
            dependencies._conda_info("example", "1.0", preferred_channel="conda-forge")
            self.assertEqual(lookup.call_count, 3)

    def test_conda_failure_is_cached_and_offline_ignores_live_cache(self):
        for channel in ("bioconda", "conda-forge"):
            with self.subTest(channel=channel):
                with patch.object(dependencies, "check_conda_package", side_effect=RuntimeError("unavailable")) as lookup:
                    for _ in range(2):
                        info = dependencies._conda_info("example", "1.0", preferred_channel=channel)
                        self.assertIsNone(info["latest_version"])
                        self.assertEqual(info["channel"], channel)
                        self.assertEqual(info["channel_url"], f"https://anaconda.org/{channel}/example")
                        self.assertIsNone(info["out_of_date"])
                    lookup.assert_called_once()
                dependencies._CONDA_CACHE[("example", "1.0", channel)]["latest_version"] = "2.0"
                with patch.object(dependencies, "check_conda_package", side_effect=AssertionError("network")):
                    info = dependencies._conda_info("example", "1.0", offline=True, preferred_channel=channel)
                self.assertIsNone(info["latest_version"])
                self.assertEqual(info["channel"], channel)
                self.assertEqual(info["channel_url"], f"https://anaconda.org/{channel}/example")
                self.assertIsNone(info["out_of_date"])

    def test_upstream_mapping_order_reuses_cached_lookup(self):
        entry = {"url": "https://github.com/example/project", "label": "Project"}
        with patch.object(dependencies, "resolve_upstream", return_value={"version": "2.0"}) as lookup:
            first = dependencies._resolve_upstream_cached(entry)
            second = dependencies._resolve_upstream_cached(dict(reversed(list(entry.items()))))
        self.assertEqual(first, second)
        lookup.assert_called_once_with(entry)

    def test_upstream_failure_is_cached_and_offline_keeps_only_explicit_version(self):
        entry = {"url": "https://github.com/example/project", "version": "1.0"}
        with patch.object(dependencies, "resolve_upstream", side_effect=RuntimeError("unavailable")) as lookup:
            self.assertIsNone(dependencies._resolve_upstream_cached(entry))
            self.assertIsNone(dependencies._resolve_upstream_cached(entry))
            lookup.assert_called_once()
        with patch.object(dependencies.requests, "get", side_effect=AssertionError("network")):
            info = dependencies._resolve_upstream_cached(entry, offline=True)
            unknown = dependencies._resolve_upstream_cached(entry["url"], offline=True)
        self.assertEqual(info["version"], "1.0")
        self.assertIsNone(unknown["version"])


if __name__ == "__main__":
    unittest.main()
