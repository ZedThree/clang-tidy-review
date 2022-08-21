#!/usr/bin/env python3

# clang-tidy review
# Copyright (c) 2020 Peter Hill
# SPDX-License-Identifier: MIT
# See LICENSE for more information

import argparse
import fnmatch
import json
import os
import pathlib
import pprint
import re
import subprocess

from post.clang_tidy_review import (
        PullRequest,
        get_line_ranges,
        get_clang_tidy_warnings,
        make_file_line_lookup,
        make_file_offset_lookup,
        message_group,
        make_review,
        cull_comments,
        strip_enclosing_quotes,
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
    dry_run: bool = False,
):

    pull_request = PullRequest(repo, pr_number, token)
    diff = pull_request.get_pr_diff()
    print(f"\nDiff from GitHub PR:\n{diff}\n")

    changed_files = [filename.target_file[2:] for filename in diff]
    files = []
    for pattern in include:
        files.extend(fnmatch.filter(changed_files, pattern))
        print(f"include: {pattern}, file list now: {files}")
    for pattern in exclude:
        files = [f for f in files if not fnmatch.fnmatch(f, pattern)]
        print(f"exclude: {pattern}, file list now: {files}")

    if files == []:
        print("No files to check!")
        return

    print(f"Checking these files: {files}", flush=True)

    line_ranges = get_line_ranges(diff, files)
    if line_ranges == "[]":
        print("No lines added in this PR!")
        return

    print(f"Line filter for clang-tidy:\n{line_ranges}\n")

    clang_tidy_warnings = get_clang_tidy_warnings(
        line_ranges,
        build_dir,
        clang_tidy_checks,
        clang_tidy_binary,
        config_file,
        '"' + '" "'.join(files) + '"',
    )
    print("clang-tidy had the following warnings:\n", clang_tidy_warnings, flush=True)

    if clang_tidy_warnings == {}:
        print("No warnings, LGTM!")
        if not dry_run:
            pull_request.post_lgtm_comment(lgtm_comment_body)
        return

    diff_lookup = make_file_line_lookup(diff)
    offset_lookup = make_file_offset_lookup(files)

    with message_group("Creating review from warnings"):
        review = make_review(
            clang_tidy_warnings["Diagnostics"], diff_lookup, offset_lookup, build_dir
        )

    print(
        "Created the following review:\n", pprint.pformat(review, width=130), flush=True
    )

    if review["comments"] == []:
        print("No warnings to report, LGTM!")
        if not dry_run:
            pull_request.post_lgtm_comment(lgtm_comment_body)
        return

    print(f"::set-output name=total_comments::{len(review['comments'])}")

    print("Removing already posted or extra comments", flush=True)
    trimmed_review = cull_comments(pull_request, review, max_comments)

    if trimmed_review["comments"] == []:
        print("Everything already posted!")
        return review

    if dry_run:
        pprint.pprint(review, width=130)
        return

    print("Posting the review:\n", pprint.pformat(trimmed_review), flush=True)
    pull_request.post_review(trimmed_review)


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
        "--clang_tidy_binary", help="clang-tidy binary", default="clang-tidy-11"
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
        dry_run=args.dry_run,
    )
