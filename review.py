#!/usr/bin/env python3

# clang-tidy review
# Copyright (c) 2020 Peter Hill
# SPDX-License-Identifier: MIT
# See LICENSE for more information

import argparse
import datetime
import itertools
import fnmatch
import json
import os
from operator import itemgetter
import pprint
import re
import requests
import subprocess
import textwrap
import unidiff
import yaml
from github import Github

BAD_CHARS_APT_PACKAGES_PATTERN = "[;&|($]"
DIFF_HEADER_LINE_LENGTH = 5
FIXES_FILE = "clang_tidy_review.yaml"


def make_file_line_lookup(diff):
    """Get a lookup table for each file in diff, to convert between source
    line number to line number in the diff

    """
    lookup = {}
    for file in diff:
        filename = file.target_file[2:]
        lookup[filename] = {}
        for hunk in file:
            for line in hunk:
                if line.diff_line_no is None:
                    continue
                if not line.is_removed:
                    lookup[filename][line.target_line_no] = (
                        line.diff_line_no - DIFF_HEADER_LINE_LENGTH
                    )
    return lookup


def make_file_offset_lookup(filenames):
    """Create a lookup table to convert between character offset and line
    number for the list of files in `filenames`.

    This is a dict of the cumulative sum of the line lengths for each file.

    """
    lookup = {}

    for filename in filenames:
        with open(filename, "r") as file:
            lines = file.readlines()
        # Length of each line
        line_lengths = map(len, lines)
        # Cumulative sum of line lengths => offset at end of each line
        lookup[os.path.abspath(filename)] = [0] + list(
            itertools.accumulate(line_lengths)
        )

    return lookup


def find_line_number_from_offset(offset_lookup, offset):
    """Work out which line number `offset` corresponds to using `offset_lookup`.

    The line number (0-indexed) is the index of the first line offset
    which is larger than `offset`.

    """
    for line_num, line_offset in enumerate(offset_lookup):
        if line_offset > offset:
            return line_num - 1
    return -1


def read_one_line(filename, line_offset):
    """Read a single line from a source file"""
    # Could cache the files instead of opening them each time?
    with open(filename, "r") as file:
        file.seek(line_offset)
        return file.readline().rstrip("\n")


def collate_replacement_sets(diagnostic, offset_lookup):
    """Return a dict of replacements on the same or consecutive lines, indexed by line number

    We need this as we have to apply all the replacements on one line at the same time

    This could break if there are replacements in with the same line
    number but in different files.

    """

    # First, make sure each replacement contains "LineNumber", and
    # "EndLineNumber" in case it spans multiple lines
    for replacement in diagnostic["Replacements"]:
        # It's possible the replacement is needed in another file?
        # Not really sure how that could come about, but let's
        # cover our behinds in case it does happen:
        if replacement["FilePath"] not in offset_lookup:
            # Let's make sure we've the file offsets for this other file
            offset_lookup.update(make_file_offset_lookup([replacement["FilePath"]]))

        replacement["LineNumber"] = find_line_number_from_offset(
            offset_lookup[replacement["FilePath"]], replacement["Offset"]
        )
        replacement["EndLineNumber"] = find_line_number_from_offset(
            offset_lookup[replacement["FilePath"]],
            replacement["Offset"] + replacement["Length"],
        )

    # Now we can group them into consecutive lines
    groups = []
    for index, replacement in enumerate(diagnostic["Replacements"]):
        if index == 0:
            # First one starts a new group, always
            groups.append([replacement])
        elif (
            replacement["LineNumber"] == groups[-1][-1]["LineNumber"]
            or replacement["LineNumber"] - 1 == groups[-1][-1]["LineNumber"]
        ):
            # Same or adjacent line to the last line in the last group
            # goes in the same group
            groups[-1].append(replacement)
        else:
            # Otherwise, start a new group
            groups.append([replacement])

    # Turn the list into a dict
    return {g[0]["LineNumber"]: g for g in groups}


def replace_one_line(replacement_set, line_num, offset_lookup):
    """Apply all the replacements in replacement_set at the same time"""

    filename = replacement_set[0]["FilePath"]
    # File offset at the start of the first line
    line_offset = offset_lookup[filename][line_num]

    # List of (start, end) offsets from line_offset
    insert_offsets = [(0, 0)]
    # Read all the source lines into a dict so we only get one copy of
    # each line, though we might read the same line in multiple times
    source_lines = {}
    for replacement in replacement_set:
        start = replacement["Offset"] - line_offset
        end = start + replacement["Length"]
        insert_offsets.append((start, end))

        # Make sure to read any extra lines we need too
        for replacement_line_num in range(
            replacement["LineNumber"], replacement["EndLineNumber"] + 1
        ):
            replacement_line_offset = offset_lookup[filename][replacement_line_num]
            source_lines[replacement_line_num] = (
                read_one_line(filename, replacement_line_offset) + "\n"
            )

    # Replacements might cross multiple lines, so squash them all together
    source_line = "".join(source_lines.values()).rstrip("\n")

    insert_offsets.append((None, None))

    fragments = []
    for (_, start), (end, _) in zip(insert_offsets[:-1], insert_offsets[1:]):
        fragments.append(source_line[start:end])

    new_line = ""
    for fragment, replacement in zip(fragments, replacement_set):
        new_line += fragment + replacement["ReplacementText"]

    return source_line, new_line + fragments[-1]


def make_comment_from_diagnostic(diagnostic_name, diagnostic, offset_lookup):
    """Create a comment from a diagnostic

    Comment contains the diagnostic message, plus its name, along with
    code block(s) containing either the exact location of the
    diagnostic, or suggested fix(es).

    """
    root = os.getcwd()
    filename = diagnostic["FilePath"]
    line_num = find_line_number_from_offset(
        offset_lookup[filename], diagnostic["FileOffset"]
    )
    line_offset = diagnostic["FileOffset"] - offset_lookup[filename][line_num]

    source_line = read_one_line(filename, offset_lookup[filename][line_num])
    end_line = line_num

    print(
        f"""{diagnostic}
    {line_num=};    {line_offset=};    {source_line=}
    """
    )

    if diagnostic["Replacements"] == []:
        # No fixit, so just point at the problem
        code_blocks = textwrap.dedent(
            f"""\
            ```cpp
            {textwrap.dedent(source_line).strip()}
            {line_offset * " " + "^"}
            ```
            """
        )
    else:
        # We're going to be appending to this
        code_blocks = ""

        replacement_sets = collate_replacement_sets(diagnostic, offset_lookup)

        for replacement_line_num, replacement_set in replacement_sets.items():
            old_line, new_line = replace_one_line(
                replacement_set, replacement_line_num, offset_lookup
            )

            print(f"----------\n{old_line=}\n{new_line=}\n----------")

            # If the replacement is for the same line as the
            # diagnostic (which is where the comment will be), then
            # format the replacement as a suggestion. Otherwise,
            # format it as a diff
            if replacement_line_num == line_num:
                code_blocks += f"""
```suggestion
{new_line}
```
"""
                end_line = replacement_set[-1]["EndLineNumber"]
            else:
                # Prepend each line in the replacement line with "+ "
                # in order to make a nice diff block. The extra
                # whitespace is so the multiline dedent-ed block below
                # doesn't come out weird.
                new_line = "\n                    ".join(
                    [f"+ {line}" for line in new_line.splitlines()]
                )

                rel_path = os.path.relpath(replacement_set[0]["FilePath"], root)
                code_blocks += textwrap.dedent(
                    f"""\

                    {rel_path}:{replacement_line_num}:
                    ```diff
                    - {old_line}
                    {new_line}
                    ```
                    """
                )

    comment_body = (
        f"warning: {diagnostic['Message']} [{diagnostic_name}]\n{code_blocks}"
    )

    return comment_body, end_line + 1


def make_review(diagnostics, diff_lookup, offset_lookup):

    root = os.getcwd()
    comments = []

    for diagnostic in diagnostics:
        try:
            diagnostic_message = diagnostic["DiagnosticMessage"]
        except KeyError:
            # Pre-clang-tidy-9 format
            diagnostic_message = diagnostic

        if diagnostic_message["FilePath"] == "":
            continue

        comment_body, end_line = make_comment_from_diagnostic(
            diagnostic["DiagnosticName"], diagnostic_message, offset_lookup
        )

        rel_path = os.path.relpath(diagnostic_message["FilePath"], root)
        # diff lines are 1-indexed
        source_line = 1 + find_line_number_from_offset(
            offset_lookup[diagnostic_message["FilePath"]],
            diagnostic_message["FileOffset"],
        )

        try:
            comments.append(
                {
                    "path": rel_path,
                    "body": comment_body,
                    "side": "RIGHT",
                    "line": end_line,
                    # "position": diff_lookup[rel_path][source_line],
                }
            )
            # If this is a multiline comment, we need a couple more bits:
            if end_line != source_line:
                comments[-1].update(
                    {
                        "start_side": "RIGHT",
                        "start_line": source_line,
                    }
                )
        except KeyError:
            print(
                f"WARNING: Skipping comment for file '{rel_path}' not in PR changeset. Comment body is:\n{comment_body}"
            )

    review = {
        "body": "clang-tidy made some suggestions",
        "event": "COMMENT",
        "comments": comments,
    }
    return review


def get_pr_diff(repo, pr_number, token):
    """Download the PR diff, return a list of PatchedFile"""

    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "Authorization": f"token {token}",
    }
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"

    pr_diff_response = requests.get(url, headers=headers)
    pr_diff_response.raise_for_status()

    # PatchSet is the easiest way to construct what we want, but the
    # diff_line_no property on lines is counted from the top of the
    # whole PatchSet, whereas GitHub is expecting the "position"
    # property to be line count within each file's diff. So we need to
    # do this little bit of faff to get a list of file-diffs with
    # their own diff_line_no range
    diff = [
        unidiff.PatchSet(str(file))[0]
        for file in unidiff.PatchSet(pr_diff_response.text)
    ]
    return diff


def get_line_ranges(diff, files):
    """Return the line ranges of added lines in diff, suitable for the
    line-filter argument of clang-tidy

    """

    lines_by_file = {}
    for filename in diff:
        if filename.target_file[2:] not in files:
            continue
        added_lines = []
        for hunk in filename:
            for line in hunk:
                if line.is_added:
                    added_lines.append(line.target_line_no)

        for _, group in itertools.groupby(
            enumerate(added_lines), lambda ix: ix[0] - ix[1]
        ):
            groups = list(map(itemgetter(1), group))
            lines_by_file.setdefault(filename.target_file[2:], []).append(
                [groups[0], groups[-1]]
            )

    line_filter_json = []
    for name, lines in lines_by_file.items():
        line_filter_json.append(str({"name": name, "lines": lines}))
    return json.dumps(line_filter_json, separators=(",", ":"))


def get_clang_tidy_warnings(
    line_filter, build_dir, clang_tidy_checks, clang_tidy_binary, files
):
    """Get the clang-tidy warnings"""

    command = f"{clang_tidy_binary} -p={build_dir} -checks={clang_tidy_checks} -line-filter={line_filter} {files} --export-fixes={FIXES_FILE}"
    print(f"Running:\n\t{command}")

    start = datetime.datetime.now()
    try:
        subprocess.run(command, shell=True, check=True, encoding="utf-8")
    except subprocess.CalledProcessError as e:
        print(
            f"\n\nclang-tidy failed with return code {e.returncode} and error:\n{e.stderr}\nOutput was:\n{e.stdout}"
        )
        raise
    end = datetime.datetime.now()

    print(f"Took: {end - start}")

    with open(FIXES_FILE, "r") as fixes_file:
        fixes = yaml.safe_load(fixes_file)

    return fixes


def post_lgtm_comment(pull_request):
    """Post a "LGTM" comment if everything's clean, making sure not to spam"""

    BODY = 'clang-tidy review says "All clean, LGTM! :+1:"'

    comments = pull_request.get_issue_comments()

    for comment in comments:
        if comment.body == BODY:
            print("Already posted, no need to update")
            return

    pull_request.create_issue_comment(BODY)


def cull_comments(pull_request, review, max_comments):
    """Remove comments from review that have already been posted, and keep
    only the first max_comments

    """

    comments = pull_request.get_review_comments()

    for comment in comments:
        review["comments"] = list(
            filter(
                lambda review_comment: not (
                    review_comment["path"] == comment.path
                    and review_comment["position"] == comment.position
                    and review_comment["body"] == comment.body
                ),
                review["comments"],
            )
        )

    if len(review["comments"]) > max_comments:
        review["body"] += (
            "\n\nThere were too many comments to post at once. "
            f"Showing the first {max_comments} out of {len(review['comments'])}. "
            "Check the log or trigger a new build to see more."
        )
        review["comments"] = review["comments"][:max_comments]

    return review


def post_review(review, repo, pr_number, token):
    # pull_request.create_review(**review)

    headers = {
        "Accept": "application/vnd.github.comfort-fade-preview+json",
        "Authorization": f"token {token}",
    }
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"

    post_review_response = requests.post(url, json=review, headers=headers)
    print(post_review_response.text)
    post_review_response.raise_for_status()


def main(
    repo,
    pr_number,
    build_dir,
    clang_tidy_checks,
    clang_tidy_binary,
    token,
    include,
    exclude,
    max_comments,
):

    diff = get_pr_diff(repo, pr_number, token)
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
        line_ranges, build_dir, clang_tidy_checks, clang_tidy_binary, " ".join(files)
    )
    print("clang-tidy had the following warnings:\n", clang_tidy_warnings, flush=True)

    diff_lookup = make_file_line_lookup(diff)
    offset_lookup = make_file_offset_lookup(files)
    review = make_review(clang_tidy_warnings["Diagnostics"], diff_lookup, offset_lookup)

    print("Created the following review:\n", pprint.pformat(review), flush=True)

    github = Github(token)
    repo_object = github.get_repo(f"{repo}")
    pull_request = repo_object.get_pull(pr_number)

    if review["comments"] == []:
        post_lgtm_comment(pull_request)
        return

    print("Removing already posted or extra comments", flush=True)
    trimmed_review = cull_comments(pull_request, review, max_comments)

    print(f"::set-output name=total_comments::{len(review['comments'])}")

    if trimmed_review["comments"] == []:
        print("Everything already posted!")
        return review

    print("Posting the review:\n", pprint.pformat(trimmed_review), flush=True)
    post_review(trimmed_review, repo, pr_number, token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a review from clang-tidy warnings"
    )
    parser.add_argument("--repo", help="Repo name in form 'owner/repo'")
    parser.add_argument("--pr", help="PR number", type=int)
    parser.add_argument(
        "--clang_tidy_binary", help="clang-tidy binary", default="clang-tidy-9"
    )
    parser.add_argument(
        "--build_dir", help="Directory with compile_commands.json", default="."
    )
    parser.add_argument(
        "--clang_tidy_checks",
        help="checks argument",
        default="'-*,performance-*,readability-*,bugprone-*,clang-analyzer-*,cppcoreguidelines-*,mpi-*,misc-*'",
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
        "--max-comments",
        help="Maximum number of comments to post at once",
        type=int,
        default=25,
    )
    parser.add_argument("--token", help="github auth token")

    args = parser.parse_args()

    # Remove any enclosing quotes and extra whitespace
    exclude = args.exclude.strip(""" "'""").split(",")
    include = args.include.strip(""" "'""").split(",")

    if args.apt_packages:
        # Try to make sure only 'apt install' is run
        apt_packages = re.split(BAD_CHARS_APT_PACKAGES_PATTERN, args.apt_packages)[
            0
        ].split(",")
        print("Installing additional packages:", apt_packages)
        subprocess.run(
            ["apt", "install", "-y", "--no-install-recommends"] + apt_packages
        )

    build_compile_commands = f"{args.build_dir}/compile_commands.json"

    if os.path.exists(build_compile_commands):
        print(f"Found '{build_compile_commands}', updating absolute paths")
        # We might need to change some absolute paths if we're inside
        # a docker container
        with open(build_compile_commands, "r") as f:
            compile_commands = json.load(f)

        original_directory = compile_commands[0]["directory"]

        # directory should either end with the build directory,
        # unless it's '.', in which case use all of directory
        if original_directory.endswith(args.build_dir):
            build_dir_index = -(len(args.build_dir) + 1)
        elif args.build_dir == ".":
            build_dir_index = -1
        else:
            raise RuntimeError(
                f"compile_commands.json contains absolute paths that I don't know how to deal with: '{original_directory}'"
            )

        basedir = original_directory[:build_dir_index]
        newbasedir = os.getcwd()

        print(f"Replacing '{basedir}' with '{newbasedir}'", flush=True)

        modified_compile_commands = json.dumps(compile_commands).replace(
            basedir, newbasedir
        )

        with open(build_compile_commands, "w") as f:
            f.write(modified_compile_commands)

    main(
        repo=args.repo,
        pr_number=args.pr,
        build_dir=args.build_dir,
        clang_tidy_checks=args.clang_tidy_checks,
        clang_tidy_binary=args.clang_tidy_binary,
        token=args.token,
        include=include,
        exclude=exclude,
        max_comments=args.max_comments,
    )
