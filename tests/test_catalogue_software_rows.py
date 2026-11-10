import unittest
from copy import deepcopy

from catalogue.rendering import detail_software_rows, env


class DetailSoftwareRowsTests(unittest.TestCase):
    def test_primary_sources_enrich_only_matching_version_and_preserve_requirements(self):
        requirements = [
            {"name": "example", "version": "1", "license": "MIT", "used_by": "First"},
            {"name": "example", "version": "2", "license": "BSD-3-Clause", "used_by": "Second"},
            {"name": "helper", "version": "3", "license": "Apache-2.0", "used_by": "All wrappers"},
        ]
        source = {"label": "GitHub", "url": "https://example.org/project", "version": "2", "newer": True}
        main = {"name": "example", "version": "1", "sources": [source]}
        originals = deepcopy((requirements, main))

        rows = detail_software_rows(requirements, main)

        self.assertEqual([(row["name"], row["version"]) for row in rows], [
            ("example", "1"), ("example", "2"), ("helper", "3"),
        ])
        self.assertEqual([row["license"] for row in rows], ["MIT", "BSD-3-Clause", "Apache-2.0"])
        self.assertEqual([row["used_by"] for row in rows], ["First", "Second", "All wrappers"])
        self.assertEqual(rows[0]["sources"], [source])
        self.assertEqual(rows[1]["sources"], [])
        rows[0]["sources"][0]["version"] = "changed"
        self.assertEqual((requirements, main), originals)

    def test_manager_keeps_related_software_and_installer_packages_but_omits_python(self):
        requirements = [
            {"name": "python", "version": "3.12", "license": "Python-2.0"},
            {"name": "unzip", "version": "6", "license": "Info-ZIP"},
        ]
        related = [{"name": "model-package", "version": "2", "sources": [], "tools": [{"name": "Predict"}]}]
        rows = detail_software_rows(requirements, requirements[0], related, is_data_manager=True)

        self.assertEqual([row["name"] for row in rows], ["model-package", "unzip"])
        self.assertEqual([row["role"] for row in rows], ["Related software", "Installer dependency"])
        self.assertEqual(rows[0]["tools"], [{"name": "Predict"}])
        self.assertEqual(rows[1]["license"], "Info-ZIP")
        self.assertEqual(detail_software_rows(requirements[:1], requirements[0], is_data_manager=True), [])

    def test_multiple_version_suite_summary_does_not_add_a_synthetic_package(self):
        requirements = [
            {"name": "example", "version": "1", "used_by": "First"},
            {"name": "example", "version": "2", "used_by": "Second"},
        ]
        main = {"name": "example", "version": "multiple", "sources": [{"label": "GitHub", "newer": True}]}

        rows = detail_software_rows(requirements, main)

        self.assertEqual([row["version"] for row in rows], ["1", "2"])
        self.assertEqual([row["sources"] for row in rows], [[], []])

    def test_manager_package_with_both_roles_keeps_installer_metadata(self):
        requirement = {"name": "example", "version": "2", "license": "MIT", "used_by": "Install"}
        related = {"name": "example", "version": "2", "sources": [{"label": "GitHub"}]}

        rows = detail_software_rows([requirement], requirement, [related], is_data_manager=True)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "Installer dependency")
        self.assertEqual(rows[0]["license"], "MIT")
        self.assertEqual(rows[0]["sources"], related["sources"])

    def test_conda_sources_keep_unknown_status_and_prerelease_version(self):
        requirements = [
            {"name": "known", "version": "1", "channel": "bioconda",
             "channel_url": "https://anaconda.org/bioconda/known", "latest_version": "2rc1", "out_of_date": True},
            {"name": "unknown", "version": "1", "channel": "bioconda",
             "channel_url": "https://anaconda.org/bioconda/unknown", "latest_version": None, "out_of_date": None},
        ]
        rows = detail_software_rows(requirements, None)

        self.assertEqual(rows[0]["sources"], [{
            "label": "bioconda", "url": "https://anaconda.org/bioconda/known", "version": "2rc1", "newer": True,
        }])
        self.assertIsNone(rows[1]["sources"][0]["version"])
        self.assertIsNone(rows[1]["sources"][0]["newer"])

    def test_missing_primary_requirement_and_duplicate_requirements_keep_available_details(self):
        main = {"name": "primary", "version": "1", "latest": "2", "channel": "bioconda",
                "channel_url": "https://anaconda.org/bioconda/primary", "out_of_date": True}
        requirements = [
            {"name": "helper", "version": "1", "license": "MIT", "used_by": "First"},
            {"name": "helper", "version": "1", "license": "MIT", "used_by": "Second"},
        ]
        rows = detail_software_rows(requirements, main)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "primary")
        self.assertEqual(rows[0]["sources"][0]["version"], "2")
        self.assertEqual(rows[1]["license"], "MIT")
        self.assertEqual(rows[1]["used_by"], "First; Second")
        self.assertIs(env.globals["detail_software_rows"], detail_software_rows)


if __name__ == "__main__":
    unittest.main()
