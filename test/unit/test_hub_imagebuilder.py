#
# koji hub plugin unit tests
#

import jsonschema
import koji
from flexmock import flexmock

from plugintest import ImageBuilderPluginTest


@ImageBuilderPluginTest.load_plugin("hub")
class TestHubPluginBuild(ImageBuilderPluginTest):

    @staticmethod
    def mock_koji_context(*, times=1, admin=False):
        session = flexmock()
        session.should_receive("hasPerm").with_args("admin").and_return(admin)

        session.should_receive("assertPerm").with_args("image").times(times)

        context = flexmock(session=session)
        return context

    @staticmethod
    def mock_kojihub(args, task):
        kojihub = flexmock()
        kojihub.should_receive("make_task").with_args(
            "imageBuilderBuild", args, **task
        )
        return kojihub

    def test_plugin_jsonschema(self):
        # Make sure the schema used to validate the input is
        # itself correct jsonschema
        schema = self.plugin.IMAGEBUILDER_BUILD_SCHEMA
        jsonschema.Draft4Validator.check_schema(schema)

    def test_basic(self):
        context = self.mock_koji_context()

        opts = {
            "repo": ["repo1", "repo2"],
            "release": "1.2.3",
            "skip_tag": True,
        }

        args = ["target", ["requested_arches"], "defs_url", "defs_path"]

        make_task_args = args + [opts]
        task = {"channel": "image"}

        kojihub = self.mock_kojihub(make_task_args, task)

        setattr(self.plugin, "context", context)
        setattr(self.plugin, "kojihub", kojihub)

        self.plugin.imageBuilderBuild(*args, opts)

    def test_input_validation(self):
        test_cases = [
            # repo without `baseurl` is not allowed
            {
                "args": [
                    "target",
                    ["requested_arches"],
                    "defs_url",
                    "defs_path",
                    ["arches"],
                ],
                "opts": {"repo": [{"package_sets": ["set1", "set2"]}]},
            },
        ]

        context = self.mock_koji_context(times=len(test_cases))
        setattr(self.plugin, "context", context)

        for idx, test_case in enumerate(test_cases):
            with self.subTest(idx=idx):
                with self.assertRaises(koji.ParameterError):
                    self.plugin.imageBuilderBuild(
                        *test_case["args"], test_case["opts"]
                    )
