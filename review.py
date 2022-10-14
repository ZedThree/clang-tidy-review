#!/usr/bin/env python3

# clang-tidy review
# Copyright (c) 2020 Peter Hill
# SPDX-License-Identifier: MIT
# See LICENSE for more information

import argparse
import json
import os
import pathlib
import re
import subprocess

from post.clang_tidy_review import (
        PullRequest,
        message_group,
        strip_enclosing_quotes,
        create_review,
        save_metadata,
        post_review,
        )


BAD_CHARS_APT_PACKAGES_PATTERN = "[;&|($]"


def main(
    repo,
    pr_number,
    build_dir,
    clang_tidy_checks,
    clang_tidy_binary,
    config_file,
    token,
    include,
    exclude,
    max_comments,
    lgtm_comment_body,
    split_workflow: bool,
    dry_run: bool = False,
):

    pull_request = PullRequest(repo, pr_number, token)

    review = create_review(
        pull_request,
        build_dir,
        clang_tidy_checks,
        clang_tidy_binary,
        config_file,
        include,
        exclude,
    )

    with message_group("Saving metadata"):
        save_metadata(pr_number)

    if split_workflow:
        print("split_workflow is enabled, not posting review")
    else:
        post_review(pull_request, review, max_comments, lgtm_comment_body, dry_run)


def fix_absolute_paths(build_compile_commands, base_dir):
    """Update absolute paths in compile_commands.json to new location, if
    compile_commands.json was created outside the Actions container
    """

    basedir = pathlib.Path(base_dir).resolve()
    newbasedir = pathlib.Path(".").resolve()

    if basedir == newbasedir:
        return

    print(f"Found '{build_compile_commands}', updating absolute paths")
    # We might need to change some absolute paths if we're inside
    # a docker container
    with open(build_compile_commands, "r") as f:
        compile_commands = json.load(f)

    print(f"Replacing '{basedir}' with '{newbasedir}'", flush=True)

    modified_compile_commands = json.dumps(compile_commands).replace(
        str(basedir), str(newbasedir)
    )

    with open(build_compile_commands, "w") as f:
        f.write(modified_compile_commands)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a review from clang-tidy warnings"
    )
    parser.add_argument("--repo", help="Repo name in form 'owner/repo'")
    parser.add_argument("--pr", help="PR number", type=int)
    parser.add_argument(
        "--clang_tidy_binary", help="clang-tidy binary", default="clang-tidy-14"
    )
    parser.add_argument(
        "--build_dir", help="Directory with compile_commands.json", default="."
    )
    parser.add_argument(
        "--base_dir",
        help="Absolute path of initial working directory if compile_commands.json generated outside of Action",
        default=".",
    )
    parser.add_argument(
        "--clang_tidy_checks",
        help="checks argument",
        default="'-*,performance-*,readability-*,bugprone-*,clang-analyzer-*,cppcoreguidelines-*,mpi-*,misc-*'",
    )
    parser.add_argument(
        "--config_file",
        help="Path to .clang-tidy config file. If not empty, takes precedence over --clang_tidy_checks",
        default="",
    )
    parser.add_argument(
        "--include",
        help="Comma-separated list of files or patterns to include",
        type=str,
        nargs="?",
        default="*.[ch],*.[ch]xx,*.[ch]pp,*.[ch]++,*.cc,*.hh",
    )
    parser.add_argument(
        "--exclude",
        help="Comma-separated list of files or patterns to exclude",
        nargs="?",
        default="",
    )
    parser.add_argument(
        "--apt-packages",
        help="Comma-separated list of apt packages to install",
        type=str,
        default="",
    )
    parser.add_argument(
        "--cmake-command",
        help="If set, run CMake as part of the action with this command",
        type=str,
        default="",
    )

    def bool_argument(user_input):
        user_input = str(user_input).upper()
        if user_input == "TRUE":
            return True
        if user_input == "FALSE":
            return False
        raise ValueError("Invalid value passed to bool_argument")

    parser.add_argument(
        "--max-comments",
        help="Maximum number of comments to post at once",
        type=int,
        default=25,
    )
    parser.add_argument(
        "--lgtm-comment-body",
        help="Message to post on PR if no issues are found. An empty string will post no LGTM comment.",
        type=str,
        default='clang-tidy review says "All clean, LGTM! :+1:"',
    )
    parser.add_argument(
        "--split_workflow",
        help="Only generate but don't post the review, leaving it for the second workflow. Relevant when receiving PRs from forks that don't have the required permissions to post reviews.",
        type=bool_argument,
        default=False,
    )
    parser.add_argument("--token", help="github auth token")
    parser.add_argument(
        "--dry-run", help="Run and generate review, but don't post", action="store_true"
    )

    args = parser.parse_args()

    # Remove any enclosing quotes and extra whitespace
    exclude = strip_enclosing_quotes(args.exclude).split(",")
    include = strip_enclosing_quotes(args.include).split(",")

    if args.apt_packages:
        # Try to make sure only 'apt install' is run
        apt_packages = re.split(BAD_CHARS_APT_PACKAGES_PATTERN, args.apt_packages)[
            0
        ].split(",")
        with message_group(f"Installing additional packages: {apt_packages}"):
            subprocess.run(
                ["apt-get", "install", "-y", "--no-install-recommends"] + apt_packages
            )

    build_compile_commands = f"{args.build_dir}/compile_commands.json"

    cmake_command = strip_enclosing_quotes(args.cmake_command)

    # If we run CMake as part of the action, then we know the paths in
    # the compile_commands.json file are going to be correct
    if cmake_command:
        with message_group(f"Running cmake: {cmake_command}"):
            subprocess.run(cmake_command, shell=True, check=True)

    elif os.path.exists(build_compile_commands):
        fix_absolute_paths(build_compile_commands, args.base_dir)

    main(
        repo=args.repo,
        pr_number=args.pr,
        build_dir=args.build_dir,
        clang_tidy_checks=args.clang_tidy_checks,
        clang_tidy_binary=args.clang_tidy_binary,
        config_file=args.config_file,
        token=args.token,
        include=include,
        exclude=exclude,
        max_comments=args.max_comments,
        lgtm_comment_body=strip_enclosing_quotes(args.lgtm_comment_body),
        split_workflow=args.split_workflow,
        dry_run=args.dry_run,
    )
