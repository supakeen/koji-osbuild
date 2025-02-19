import os


import koji

from koji.tasks import BaseTaskHandler, ServerExit


class ImageBuilderBuildTask(BaseTaskHandler):
    """Sets up multiple `ImageBuilderCreateImageTask`s, one for each
    requested architecture. Monitors those tasks for failures."""

    Methods = ["imageBuilderBuild"]

    def handler(
        self,
        target,
        requested_arches,
        defs_url,
        defs_path,
        opts=None,
    ):
        target = self.session.getBuildTarget(target, strict=True)

        if not target:
            raise koji.BuildError(f"unknown target '{target}' not found")

        btag = target["build_tag"]

        conf = self.session.getBuildConfig(btag)

        if not conf["arches"]:
            raise koji.BuildError(
                f"no arches for tag {conf['name']!r} [{conf['id']}]"
            )

        tag_x_arch = [koji.canonArch(arch) for arch in conf["arches"].split()]

        if requested_arches:
            for arch in requested_arches:
                if koji.canonArch(arch) not in tag_x_arch:
                    raise koji.BuildError(
                        "unsupported arch for build tag: %s" % arch
                    )
        else:
            requested_arches = tag_x_arch

        distribution = opts.get("distribution")
        version = opts.get("version")
        release = opts.get("release")
        image_type = opts.get("image_type")

        # repo = self.getRepo(btag)
        repo = ""

        if not opts:
            opts = {}

        opts.setdefault("scratch", False)
        opts.setdefault("optional_arches", [])

        if not conf.get("extra", {}).get("mock.new_chroot", True):
            opts["mount_dev"] = True

        self.opts = opts

        # XXX We should do a SCM checkout here, the SCM will eventually contain the
        # XXX definitions as owned, and used, by Fedora. Right now we *don't* do a
        # XXX check out yet and instead use the built-in definitions as provided by
        # XXX `image-builder`.

        # XXX Fedora has come from 'somewhere'
        name = "Fedora-%s" % image_type

        if opts.get("version"):
            version = opts["version"]

        release = opts.get("release") or self.session.getNextRelease(
            {"name": name, "version": version}
        )

        bld_info = {}

        if not opts["scratch"]:
            bld_info = self.initImageBuild(name, version, release, target, opts)
            release = bld_info["release"]

        # prepare for tasks
        subtasks = {}
        canfails = []

        self.logger.debug(
            "Spawning jobs for image arches: %r" % (requested_arches)
        )

        try:
            for arch in requested_arches:
                subtasks[arch] = self.session.host.subtask(
                    method="imageBuilderCreateImage",
                    arglist=[
                        name,
                        version,
                        release,
                        arch,
                        target,
                        btag,
                        repo,
                        defs_url,
                        defs_path,
                        opts,
                    ],
                    label=arch,
                    parent=self.id,
                    arch=arch,
                )

                if arch in self.opts["optional_arches"]:
                    canfails.append(subtasks[arch])

            self.logger.debug("Got image subtasks: %r" % (subtasks))
            self.logger.debug(
                "Waiting on image subtasks (%s can fail)..." % canfails
            )

            results = self.wait(
                list(subtasks.values()),
                all=True,
                failany=True,
                canfail=canfails,
            )

            # if everything failed, fail even if all subtasks are in canfail
            self.logger.debug("subtask results: %r", results)
            all_failed = True

            for result in results.values():
                if not isinstance(result, dict) or "faultCode" not in result:
                    all_failed = False
                    break

            if all_failed:
                raise koji.GenericError("all subtasks failed")

            # determine ignored arch failures
            ignored_arches = set()

            for arch in requested_arches:
                if arch in self.opts["optional_arches"]:
                    task_id = subtasks[arch]
                    result = results[task_id]
                    if isinstance(result, dict) and "faultCode" in result:
                        ignored_arches.add(arch)

            self.logger.debug("Image Results for hub: %s" % results)
            results = {str(k): v for k, v in results.items()}

            if opts["scratch"]:
                self.session.host.moveImageBuildToScratch(self.id, results)
            else:
                self.session.host.completeImageBuild(
                    self.id, bld_info["id"], results
                )
        except (SystemExit, ServerExit, KeyboardInterrupt):
            # we do not trap these
            raise
        except Exception:
            if not opts["scratch"]:
                if bld_info:
                    self.session.host.failBuild(self.id, bld_info["id"])
            raise

        # tag it
        if not opts["scratch"] and not opts.get("skip_tag"):
            tag_task_id = self.session.host.subtask(
                method="tagBuild",
                arglist=[target["dest_tag"], bld_info["id"], False, None, True],
                label="tag",
                parent=self.id,
                arch="noarch",
            )

            self.wait(tag_task_id)

        # report results
        report = ""

        if opts["scratch"]:
            respath = ", ".join(
                [
                    os.path.join(
                        koji.pathinfo.work(), koji.pathinfo.taskrelpath(tid)
                    )
                    for tid in subtasks.values()
                ]
            )

            report += "Scratch "
        else:
            respath = koji.pathinfo.imagebuild(bld_info)

        report += "image build results in: %s" % respath

        return report

    # This is duplicated directly from `koji`'s `kojid` binary, there is no
    # straightforward way to include from there so we have to do a bit of
    # duplication.
    #
    # This can be removed once we move this plugin into `koji` itself after
    # it stabilizes.
    def initImageBuild(self, name, version, release, target_info, opts):
        """create a build object for this image build"""
        pkg_cfg = self.session.getPackageConfig(
            target_info["dest_tag_name"], name
        )
        self.logger.debug("%r" % pkg_cfg)
        if not opts.get("skip_tag") and not opts.get("scratch"):
            # Make sure package is on the list for this tag
            if pkg_cfg is None:
                raise koji.BuildError(
                    "package (image) %s not in list for tag %s"
                    % (name, target_info["dest_tag_name"])
                )
            elif pkg_cfg["blocked"]:
                raise koji.BuildError(
                    "package (image)  %s is blocked for tag %s"
                    % (name, target_info["dest_tag_name"])
                )
        return self.session.host.initImageBuild(
            self.id, dict(name=name, version=version, release=release, epoch=0)
        )
