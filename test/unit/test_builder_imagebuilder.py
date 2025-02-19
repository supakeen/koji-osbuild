# pylint: disable=too-many-lines

from flexmock import flexmock

import koji

from plugintest import ImageBuilderPluginTest


@ImageBuilderPluginTest.load_plugin("builder")
class TestBuilderPluginImageBuilderBuild(
    ImageBuilderPluginTest
):  # pylint: disable=too-many-public-methods
    def test_unknown_build_target(self):
        session = flexmock()
        options = mock_options()

        # We expect a call to `getBuildTarget` however this will return None
        # which causes the task to error.
        session.should_receive("getBuildTarget").with_args(
            "target", strict=True
        ).and_return(None)

        handler = self.plugin.ImageBuilderBuildTask(
            1, "imageBuilderBuild", "params", session, options
        )

        args = [
            "target",
            ["arches"],
            "defs_url",
            "defs_path",
            {"repo": ["https://1.repo"]},
        ]

        with self.assertRaises(koji.BuildError) as err:
            handler.handler(*args)

        self.assertTrue(str(err.exception).startswith("unknown target"))

    def test_no_architectures(self):
        session = flexmock()
        options = mock_options()

        build_target = {
            "build_tag": "fedora-build",
            "name": "fedora-candidate",
            "dest_tag_name": "fedora-updates",
        }

        session.should_receive("getBuildTarget").with_args(
            "fedora-candidate", strict=True
        ).and_return(build_target).once()

        session.should_receive("getBuildConfig").with_args(
            build_target["build_tag"]
        ).and_return({"arches": "", "id": "", "name": ""})

        handler = self.plugin.ImageBuilderBuildTask(
            1, "imageBuilderBuild", "params", session, options
        )

        args = [
            "fedora-candidate",
            ["arches"],
            "defs_url",
            "defs_path",
            {"repo": ["https://1.repo"]},
        ]

        with self.assertRaises(koji.BuildError) as err:
            handler.handler(*args)

        self.assertTrue(str(err.exception).startswith("no arches"))

    def test_unsupported_architecture(self):
        session = flexmock()

        build_target = {
            "build_tag": "fedora-build",
            "name": "fedora-candidate",
            "dest_tag_name": "fedora-updates",
        }

        session.should_receive("getBuildTarget").with_args(
            "fedora-candidate", strict=True
        ).and_return(build_target).once()

        session.should_receive("getBuildConfig").with_args(
            build_target["build_tag"]
        ).and_return({"arches": "x86_64"})

        options = flexmock(allowed_scms="pkg.osbuild.org:/*:no", workdir="/tmp")

        handler = self.plugin.ImageBuilderBuildTask(
            1, "imageBuilderBuild", "params", session, options
        )

        args = [
            "fedora-candidate",
            ["s390x"],
            "defs_url",
            "defs_path",
            {"repo": ["https://1.repo"]},
        ]

        with self.assertRaises(koji.BuildError) as err:
            handler.handler(*args)

        self.assertTrue(str(err.exception).startswith("unsupported arch"))

    def test_smoke(self):
        session = mock_session()
        options = mock_options()

        handler = self.plugin.ImageBuilderBuildTask(
            1, "imageBuilderBuild", "params", session, options
        )

        args = [
            "fedora-candidate",
            ["x86_64"],
            "defs_url",
            "defs_path",
            {"repo": ["https://1.repo"]},
        ]

        handler.handler(*args)


class MockHost:
    """Mock for the HostExport koji class

    HostExport has the builder specific XML-RPC methods. This mocks a
    small subset of it. Currently the methods to support tagging a
    build are supported.
    The `tags` property, a mapping from build it to a list of tag ids,
    can be used see what tags were applied to a build id.
    """

    def __init__(self):
        self.tasks = {}
        self.waitset = {}
        self.count = 0
        self.tags = {}

        self.create_image_tasks = {}

    def subtask(self, method, arglist, parent, **opts):
        task = {
            "method": method,
            "parent": parent,
            "arglist": arglist,
            "opts": opts,
            "result": True,
        }

        if method == "tagBuild":
            self._tag_build(task)
        elif method == "imageBuilderCreateImage":
            self._image_builder_create_image(task)
        else:
            raise ValueError(f"{method} not mocked")

        self.count += 1
        task_id = self.count
        self.tasks[task_id] = task

        return task_id

    def taskSetWait(self, parent, tasks):
        if tasks is None:
            tasks = [k for k, v in self.tasks.items() if v["parent"] == parent]

        self.waitset[parent] = tasks

    def taskWait(self, parent):
        tasks = self.waitset[parent]
        return tasks, []

    def taskWaitResults(self, parent, tasks, canfail=None):
        if canfail is None:
            canfail = []
        waitset = self.waitset[parent]
        selected = [t for t in waitset if t in tasks]
        res = {t: self.tasks[t]["result"] for t in selected}
        return res

    def initImageBuild(self, parent, opts):
        opts["id"] = 42
        return opts

    def completeImageBuild(self, task_id, build_id, results):
        return

    def failBuild(self, p0, p1):
        pass

    def _tag_build(self, task):
        assert task["parent"], "tagBuild: need parent"
        args = task["arglist"]
        assert 2 < len(args) < 6, "tagBuild: wrong argument number"
        tag = args[0]
        build = args[1]
        assert isinstance(tag, int), "tagBuild: tag id not int"
        assert isinstance(build, int), "tagBuild: build id not int"

        tags = self.tags.get(build, [])
        tags += [tag]
        self.tags[build] = tags

    def _image_builder_create_image(self, task):
        assert task["parent"], "imageBuilderCreateImage: need parent"

        args = task["arglist"]


def mock_session():
    host = MockHost()
    session = flexmock(host=host)

    build_target = {
        "build_tag": 23,
        "build_tag_name": "fedora-build",
        "dest_tag": 42,
        "dest_tag_name": "fedora-dest",
    }

    tag_info = {
        "id": build_target["dest_tag"],
        "name": build_target["dest_tag_name"],
        "locked": False,
    }

    build_config = {"arches": "s390x aarch64 ppc64le x86_64"}

    repo_info = {
        "id": 20201015,
        "tag": build_target["build_tag_name"],
        "tag_id": build_target["build_tag"],
        "event_id": 2121,
    }

    session.should_receive("getBuildTarget").with_args(
        "fedora-candidate", strict=True
    ).and_return(build_target)

    session.should_receive("getBuildConfig").with_args(
        build_target["build_tag"]
    ).and_return(build_config)

    session.should_receive("getTag").with_args(
        build_target["build_tag"], strict=True
    ).and_return(tag_info)

    session.should_receive("getRepo").with_args(
        build_target["build_tag"]
    ).and_return(repo_info)

    session.should_receive("getNextRelease").with_args(dict).and_return(
        "20201015"
    )

    session.should_receive("getPackageConfig").with_args(
        build_target["dest_tag_name"], str
    ).and_return({"blocked": []})

    return session


def mock_options():
    options = flexmock(
        allowed_scms="pkg.osbuild.org:/*:no",
        workdir="/tmp",
        topurl="http://localhost/kojifiles",
    )
    return options
