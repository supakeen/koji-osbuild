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
        requested_arches: str,
        defs_url,
        defs_path,
        opts=None,
    ):
        """Bladiebla

        target:
        requested_arches:

        defs_url:
        defs_path:

        opts:
        """


        # The build target specifies in what context this build should take
        # place. We should ensure that the target that was passed to us
        # actually exists.
        target = self.session.getBuildTarget(target, strict=True)

        if not target:
            raise koji.BuildError(f"unknown target '{target}' not found")

        # XXX There's a separate context that we use to build in. Explain
        # XXX the difference.
        btag = target["build_tag"]

        # ...
        conf = self.session.getBuildConfig(btag)

        # Make sure our build target has architectures configured.
        if not conf["arches"]:
            raise koji.BuildError(
                f"no arches for tag {conf['name']!r} [{conf['id']}]"
            )

        tag_x_arch = [koji.canonArch(arch) for arch in conf["arches"].split()]

        # Verify that all the requested arches for this build are available in
        # our build target. If they're not then we error. If no arches are
        # requested then we build all the available arches in the build tag.
        if requested_arches:
            for arch in requested_arches:
                if koji.canonArch(arch) not in tag_x_arch:
                    raise koji.BuildError(
                        "unsupported arch for build tag: %s" % arch
                    )
        else:
            requested_arches = tag_x_arch

        # Information that's actually meant for `image-builder` itself comes in
        # through the options.
        distribution = opts.get("distribution")
        version = opts.get("version")
        release = opts.get("release")
        image_type = opts.get("image_type")

        # repo = self.getRepo(btag)
        repo = ""

        # Handle the optional `opts`
        if opts is None:
            opts = {}

        # Scratch builds are builds that end up not being tagged.
        opts.setdefault("scratch", False)

        # While we might build a whole bunch of arches some of them might not
        # be required to actually succeed.
        opts.setdefault("optional_arches", [])

        # XXX wut?
        if not conf.get("extra", {}).get("mock.new_chroot", True):
            opts["mount_dev"] = True

        # XXX why?
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

        # We're going to be spawning subtasks and we want to do some
        # housekeeping around that. We'll keep track of our subtasks and keep a
        # list of the allowed-to-fail tasks.
        subtasks = {}
        canfails = []

        self.logger.debug(
            "Spawning jobs for image arches: %r" % (requested_arches)
        )

        try:
            # We create a new subtask for every requested architecture, these
            # subtasks are the ones that actually run `image-builder`.
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

                # If the requested architecture is optional then we allow for
                # the subtask to fail.
                if arch in self.opts["optional_arches"]:
                    canfails.append(subtasks[arch])

            self.logger.debug("Got image subtasks: %r" % (subtasks))
            self.logger.debug(
                "Waiting on image subtasks (%s can fail)..." % canfails
            )

            # XXX Wait for the subtasks to complete. NEEDS EXPANSION.
            results = self.wait(
                list(subtasks.values()),
                all=True,
                failany=True,
                canfail=canfails,
            )

            # if everything failed, fail even if all subtasks are in canfail
            self.logger.debug("subtask results: %r", results)

            # Test if all the results were failed. We go through all of them
            # and test their result type and data. If we don't break the `else`
            # clause of the for loop triggers an error.
            for result in results.values():
                if not isinstance(result, dict) or "faultCode" not in result:
                    break
            else:
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

        # If this wasn't a scratch build, and we're not instructed to skip the
        # tag step then we also create a new `tagBuild` task.
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
        """Creates a build object for this image build."""

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


class ImageBuilderCreateImageTask(BaseTaskHandler):
    """Perform an image build on a builder."""

    Methods = ["imageBuilderCreateImage"]

    def handler(
        self,
        name,
        version,
        release,
        arch,
        target,
        build_tag,
        repo_info,
        defs_url,
        defs_path,
        opts=None,
    ):

        bind_opts = {'dirs': {}}

        if self.opts.get('mount_dev'):
            bind_opts['dirs']['/dev'] = '/dev'

        return

        """
        broot = BuildRoot(
            self.session,
            self.options,
            tag=build_tag,
            arch=arch,
            task_id=self.id,
            repo_id=repo_info['id'],
            install_group='image-builder-build',
            setup_dns=True,
            bind_opts=bind_opts
        )

        broot.init()
        # Set up the default call to `image-builder`, we'll include a bunch of
        # extra artifacts to upload together with the produced result.

        # TODO: add `--with-buildlog` when in `image-builder`
        cmd = [
            "image-builder", "build", "-v",
            "--with-sbom",
            "--with-manifest",
            "--force-repo", repo_url,
            "--distro", f"{distribution}-{version}",
            image_type,
        ]

        # TODO: upload
        print(cmd)
        """

        self.opts = opts
        build_tag = target_info['build_tag']
        bind_opts = {'dirs': {}}
        if self.opts.get('mount_dev'):
            bind_opts['dirs']['/dev'] = '/dev'
        broot = BuildRoot(self.session, self.options,
                          tag=build_tag,
                          arch=arch,
                          task_id=self.id,
                          repo_id=repo_info['id'],
                          install_group='kiwi-build',
                          setup_dns=True,
                          bind_opts=bind_opts)
        broot.workdir = self.workdir

        # create the mock chroot
        self.logger.debug("Initializing kiwi buildroot")
        broot.init()
        self.logger.debug("Kiwi buildroot ready: " + broot.rootdir())

        # get configuration
        scm = SCM(desc_url)
        scm.assert_allowed(allowed=self.options.allowed_scms,
                           session=self.session,
                           by_config=self.options.allowed_scms_use_config,
                           by_policy=self.options.allowed_scms_use_policy,
                           policy_data={
                               'user_id': self.taskinfo['owner'],
                               'channel': self.session.getChannel(self.taskinfo['channel_id'],
                                                                  strict=True)['name'],
                               'scratch': self.opts.get('scratch', False)
                           })
        logfile = os.path.join(self.workdir, 'checkout-%s.log' % arch)
        self.run_callbacks('preSCMCheckout', scminfo=scm.get_info(),
                           build_tag=build_tag, scratch=self.opts.get('scratch', False))
        scmdir = broot.tmpdir()
        koji.ensuredir(scmdir)
        scmsrcdir = scm.checkout(scmdir, self.session,
                                 self.getUploadDir(), logfile)
        self.run_callbacks("postSCMCheckout",
                           scminfo=scm.get_info(),
                           build_tag=build_tag,
                           scratch=self.opts.get('scratch', False),
                           srcdir=scmsrcdir)

        # user repos
        repos = self.opts.get('repos', [])
        if self.opts.get('use_buildroot_repo', False):
            path_info = koji.PathInfo(topdir=self.options.topurl)
            repopath = path_info.repo(repo_info['id'], target_info['build_tag_name'])
            baseurl = '%s/%s' % (repopath, arch)
            self.logger.debug('BASEURL: %s' % baseurl)
            repos.append(baseurl)
        repo_releasever = self.opts.get('repo_releasever', version)

        base_path = os.path.dirname(desc_path)
        if opts.get('make_prep'):
            cmd = ['make', 'prep']
            rv = broot.mock(['--cwd', os.path.join(broot.tmpdir(within=True),
                                                   os.path.basename(scmsrcdir), base_path),
                             '--chroot', '--'] + cmd)
            if rv:
                raise koji.GenericError("Preparation step failed")

        path = os.path.join(scmsrcdir, desc_path)
        desc, types = self.prepareDescription(path, name, version, repos, repo_releasever, arch)
        self.uploadFile(desc)

        target_dir = '/builddir/result/image'
        os.symlink(  # symlink log to resultdir, so it is incrementally uploaded
            os.path.join(broot.rootdir(), f'tmp/image-root.{arch}.log'),
            os.path.join(broot.resultdir(), f'image-root.{arch}.log')
        )
        cmd = ['kiwi-ng']
        if self.opts.get('profile'):
            cmd.extend(['--profile', self.opts['profile']])
        if self.opts.get('type'):
            cmd.extend(['--type', self.opts['type']])
        cmd.extend([
            '--kiwi-file', os.path.basename(desc),  # global option for image/system commands
            '--debug',
            '--logfile', f'/tmp/image-root.{arch}.log',
            'system', 'build',
            '--description', os.path.join(os.path.basename(scmsrcdir), base_path),
            '--target-dir', target_dir,
        ])
        for typeattr in self.opts.get('type_attr', []):
            cmd.extend(['--set-type-attr', typeattr])
        rv = broot.mock(['--cwd', broot.tmpdir(within=True), '--chroot', '--'] + cmd)
        if rv:
            raise koji.GenericError("Kiwi failed")

        # rename artifacts accordingly to release
        os.symlink(  # symlink log to resultdir, so it is incrementally uploaded
            os.path.join(broot.rootdir(), f'/tmp/kiwi-result-bundle.{arch}.log'),
            os.path.join(broot.resultdir(), f'kiwi-result-bundle.{arch}.log')
        )
        bundle_dir = '/builddir/result/bundle'
        cmd = ['kiwi-ng',
               '--debug',
               '--logfile', f'/tmp/kiwi-result-bundle.{arch}.log',
               'result', 'bundle',
               '--target-dir', target_dir,
               '--bundle-dir', bundle_dir,
               '--id', release]
        if self.opts.get('result_bundle_name_format'):
            cmd.extend(['--bundle-format', self.opts['result_bundle_name_format']])
        rv = broot.mock(['--cwd', broot.tmpdir(within=True), '--chroot', '--'] + cmd)
        if rv:
            raise koji.GenericError("Kiwi failed")

        imgdata = {
            'arch': arch,
            'task_id': self.id,
            'logs': [
                os.path.basename(desc),
            ],
            'name': name,
            'version': version,
            'release': release,
            'rpmlist': [],
            'files': [],
        }

        bundle_path = os.path.join(broot.rootdir(), bundle_dir[1:])
        for fname in os.listdir(bundle_path):
            self.uploadFile(os.path.join(bundle_path, fname), remoteName=fname)
            imgdata['files'].append(fname)

        if not self.opts.get('scratch'):
            if False:
                # should be used after kiwi update
                fpath = os.path.join(
                    bundle_path,
                    next(f for f in imgdata['files'] if f.endswith('.packages')),
                )
                hdrlist = self.getImagePackages(fpath)
            else:
                cachepath = os.path.join(broot.rootdir(), 'var/cache/kiwi/dnf')
                hdrlist = self.getImagePackagesFromCache(cachepath)
            broot.markExternalRPMs(hdrlist)
            imgdata['rpmlist'] = hdrlist

        broot.expire()

        self.logger.error("Uploading image data: %s", imgdata)
        return imgdata
