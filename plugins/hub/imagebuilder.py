"""Koji osbuild integration for Koji Hub"""

import sys
import logging
import jsonschema

import koji
from koji.context import context

sys.path.insert(0, "/usr/share/koji-hub/")
import kojihub  # pylint: disable=import-error, wrong-import-position


logger = logging.getLogger("koji.plugin.osbuild")


IMAGEBUILDER_BUILD_SCHEMA = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "title": "imageBuilderBuild arguments",
    "type": "array",
    "minItems": 4,
    "items": [
        {"type": "string", "description": "Target"},
        {"type": "array", "description": "Requested Architectures"},
        {"type": "string", "description": "Definitions URL"},
        {"type": "string", "description": "Definitions Path"},
        {"type": "object", "$ref": "#/definitions/options"},
    ],
    "definitions": {
        "repo": {
            "title": "Repository options",
            "type": "object",
            "additionalProperties": False,
            "required": ["baseurl"],
            "properties": {
                "baseurl": {"type": "string"},
                "package_sets": {
                    "type": "array",
                    "description": "Repositories",
                    "items": {"type": "string"},
                },
            },
        },
        "ostree": {
            "title": "OSTree specific options",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "parent": {"type": "string"},
                "ref": {"type": "string"},
                "url": {"type": "string"},
            },
        },
        "options": {
            "title": "Optional arguments",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "customizations": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "ostree": {"type": "object", "$ref": "#/definitions/ostree"},
                "repo": {
                    "type": "array",
                    "description": "Repositories",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"$ref": "#/definitions/repo"},
                        ]
                    },
                },
                "release": {
                    "type": "string",
                    "description": "Release override",
                },
                "skip_tag": {
                    "type": "boolean",
                    "description": "Omit tagging the result",
                },
            },
        },
    },
}


@koji.plugin.export
def imageBuilderBuild(
    target, requested_arches, defs_url, defs_path, opts=None, priority=None
):
    """Create an image via image-builder"""
    context.session.assertPerm("image")
    args = [target, requested_arches, defs_url, defs_path, opts]
    task = {"channel": "image"}

    logger.info("Create imageBuilderBuild task")

    try:
        jsonschema.validate(args, IMAGEBUILDER_BUILD_SCHEMA)
    except jsonschema.exceptions.ValidationError as err:
        raise koji.ParameterError(str(err)) from None

    if priority and priority < 0 and not context.session.hasPerm("admin"):
        raise koji.ActionNotAllowed(
            "only admins may create high-priority tasks"
        )

    # If task_id is returned from Koji Hub we assume
    # that the task has been added to the database
    task_id = kojihub.make_task("imageBuilderBuild", args, **task)
    if task_id:
        logger.info("imageBuilderBuild task %i added to database", task_id)

    return task_id
