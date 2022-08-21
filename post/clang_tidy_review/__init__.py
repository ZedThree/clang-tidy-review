# clang-tidy review
# Copyright (c) 2020 Peter Hill
# SPDX-License-Identifier: MIT
# See LICENSE for more information

import argparse
import itertools
import json
import os
from operator import itemgetter
import pprint
import pathlib
import requests
import subprocess
import textwrap
import unidiff
import yaml
import contextlib
import datetime
import subprocess
from github import Github
from github.Requester import Requester
from github.PaginatedList import PaginatedList
from typing import Any, List, Optional, TypedDict

DIFF_HEADER_LINE_LENGTH = 5
FIXES_FILE = "clang_tidy_review.yaml"
METADATA_FILE = "clang-tidy-review-metadata.json"


class Metadata(TypedDict):
    """Loaded from `METADATA_FILE`
    Contains information necessary to post a review without pull request knowledge

    """

    pr_number: int


class PullRequest:
    """Add some convenience functions not in PyGithub"""

    def __init__(self, repo: str, pr_number: int, token: str) -> None:
        self.repo = repo
        self.pr_number = pr_number
        self.token = token

        github = Github(token)
        repo_object = github.get_repo(f"{repo}")
        self._pull_request = repo_object.get_pull(pr_number)

    def headers(self, media_type: str):
        return {
            "Accept": f"application/vnd.github.{media_type}",
            "Authorization": f"token {self.token}",
        }

    @property
    def base_url(self):
        return f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"

    def get(self, media_type: str, extra: str = "") -> str:
        url = f"{self.base_url}{extra}"
        response = requests.get(url, headers=self.headers(media_type))
        response.raise_for_status()
        return response.text

    def get_pr_diff(self) -> List[unidiff.PatchSet]:
        """Download the PR diff, return a list of PatchedFile"""

        diffs = self.get("v3.diff")

        # PatchSet is the easiest way to construct what we want, but the
        # diff_line_no property on lines is counted from the top of the
        # whole PatchSet, whereas GitHub is expecting the "position"
        # property to be line count within each file's diff. So we need to
        # do this little bit of faff to get a list of file-diffs with
        # their own diff_line_no range
        diff = [unidiff.PatchSet(str(file))[0] for file in unidiff.PatchSet(diffs)]
        return diff

    def get_pr_comments(self):
        """Download the PR review comments using the comfort-fade preview headers"""

        def get_element(
            requester: Requester, headers: dict, element: dict, completed: bool
        ):
            return element

        return PaginatedList(
            get_element,
            self._pull_request._requester,
            f"{self.base_url}/comments",
            None,
        )

    def post_lgtm_comment(self, body: str):
        """Post a "LGTM" comment if everything's clean, making sure not to spam"""

        if not body:
            return

        comments = self.get_pr_comments()

        for comment in comments:
            if comment["body"] == body:
                print("Already posted, no need to update")
                return

        self._pull_request.create_issue_comment(body)

    def post_review(self, review):
        """Submit a completed review"""
        headers = {
            "Accept": "application/vnd.github.comfort-fade-preview+json",
            "Authorization": f"token {self.token}",
        }
        url = f"{self.base_url}/reviews"

        post_review_response = requests.post(url, json=review, headers=headers)
        print(post_review_response.text)
        post_review_response.raise_for_status()


@contextlib.contextmanager
def message_group(title: str):
    print(f"::group::{title}", flush=True)
    try:
        yield
    finally:
        print("::endgroup::", flush=True)


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


def get_diagnostic_file_path(clang_tidy_diagnostic, build_dir):

    # Sometimes, clang-tidy gives us an absolute path, so everything is fine.
    # Sometimes however it gives us a relative path that is realtive to the
    # build directory, so we prepend that.

    # Modern clang-tidy
    if ("DiagnosticMessage" in clang_tidy_diagnostic) and (
        "FilePath" in clang_tidy_diagnostic["DiagnosticMessage"]
    ):
        file_path = clang_tidy_diagnostic["DiagnosticMessage"]["FilePath"]
        if file_path == "":
            return ""
        elif os.path.isabs(file_path):
            return os.path.normpath(os.path.abspath(file_path))
        else:
            # Make the relative path absolute with the build dir
            if "BuildDirectory" in clang_tidy_diagnostic:
                return os.path.normpath(
                    os.path.abspath(
                        os.path.join(clang_tidy_diagnostic["BuildDirectory"], file_path)
                    )
                )
            else:
                return os.path.normpath(os.path.abspath(file_path))

    # Pre-clang-tidy-9 format
    elif "FilePath" in clang_tidy_diagnostic:
        file_path = clang_tidy_diagnostic["FilePath"]
        if file_path == "":
            return ""
        else:
            return os.path.normpath(os.path.abspath(os.path.join(build_dir, file_path)))

    else:
        return ""


def find_line_number_from_offset(offset_lookup, filename, offset):
    """Work out which line number `offset` corresponds to using `offset_lookup`.

    The line number (0-indexed) is the index of the first line offset
    which is larger than `offset`.

    """
    name = str(pathlib.Path(filename).resolve().absolute())

    if name not in offset_lookup:
        # Let's make sure we've the file offsets for this other file
        offset_lookup.update(make_file_offset_lookup([name]))

    for line_num, line_offset in enumerate(offset_lookup[name]):
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
            offset_lookup, replacement["FilePath"], replacement["Offset"]
        )
        replacement["EndLineNumber"] = find_line_number_from_offset(
            offset_lookup,
            replacement["FilePath"],
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


def format_ordinary_line(source_line, line_offset):
    """Format a single C++ line with a diagnostic indicator"""

    return textwrap.dedent(
        f"""\
         ```cpp
         {source_line}
         {line_offset * " " + "^"}
         ```
         """
    )


def format_diff_line(diagnostic, offset_lookup, source_line, line_offset, line_num):
    """Format a replacement as a Github suggestion or diff block"""

    end_line = line_num

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
            whitespace = "\n                "
            new_line = whitespace.join([f"+ {line}" for line in new_line.splitlines()])
            old_line = whitespace.join([f"- {line}" for line in old_line.splitlines()])

            rel_path = try_relative(replacement_set[0]["FilePath"])
            code_blocks += textwrap.dedent(
                f"""\

                {rel_path}:{replacement_line_num}:
                ```diff
                {old_line}
                {new_line}
                ```
                """
            )
    return code_blocks, end_line


def try_relative(path):
    """Try making `path` relative to current directory, otherwise make it an absolute path"""
    try:
        here = pathlib.Path.cwd()
        return pathlib.Path(path).relative_to(here)
    except ValueError:
        return pathlib.Path(path).resolve()


def format_notes(notes, offset_lookup):
    """Format an array of notes into a single string"""

    code_blocks = ""

    for note in notes:
        filename = note["FilePath"]

        if filename == "":
            return note["Message"]

        resolved_path = str(pathlib.Path(filename).resolve().absolute())

        line_num = find_line_number_from_offset(
            offset_lookup, resolved_path, note["FileOffset"]
        )
        line_offset = note["FileOffset"] - offset_lookup[resolved_path][line_num]
        source_line = read_one_line(
            resolved_path, offset_lookup[resolved_path][line_num]
        )

        path = try_relative(resolved_path)
        message = f"**{path}:{line_num}:** {note['Message']}"
        code = format_ordinary_line(source_line, line_offset)
        code_blocks += f"{message}\n{code}"

    return code_blocks


def make_comment_from_diagnostic(
    diagnostic_name, diagnostic, filename, offset_lookup, notes
):
    """Create a comment from a diagnostic

    Comment contains the diagnostic message, plus its name, along with
    code block(s) containing either the exact location of the
    diagnostic, or suggested fix(es).

    """

    line_num = find_line_number_from_offset(
        offset_lookup, filename, diagnostic["FileOffset"]
    )
    line_offset = diagnostic["FileOffset"] - offset_lookup[filename][line_num]

    source_line = read_one_line(filename, offset_lookup[filename][line_num])
    end_line = line_num

    print(
        f"""{diagnostic}
    {line_num=};    {line_offset=};    {source_line=}
    """
    )

    if diagnostic["Replacements"]:
        code_blocks, end_line = format_diff_line(
            diagnostic, offset_lookup, source_line, line_offset, line_num
        )
    else:
        # No fixit, so just point at the problem
        code_blocks = format_ordinary_line(source_line, line_offset)

    code_blocks += format_notes(notes, offset_lookup)

    comment_body = (
        f"warning: {diagnostic['Message']} [{diagnostic_name}]\n{code_blocks}"
    )

    return comment_body, end_line + 1


def make_review(diagnostics, diff_lookup, offset_lookup, build_dir):
    """Create a Github review from a set of clang-tidy diagnostics"""

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
            diagnostic["DiagnosticName"],
            diagnostic_message,
            get_diagnostic_file_path(diagnostic, build_dir),
            offset_lookup,
            notes=diagnostic.get("Notes", []),
        )

        rel_path = str(try_relative(get_diagnostic_file_path(diagnostic, build_dir)))
        # diff lines are 1-indexed
        source_line = 1 + find_line_number_from_offset(
            offset_lookup,
            get_diagnostic_file_path(diagnostic, build_dir),
            diagnostic_message["FileOffset"],
        )

        if rel_path not in diff_lookup or end_line not in diff_lookup[rel_path]:
            print(
                f"WARNING: Skipping comment for file '{rel_path}' not in PR changeset. Comment body is:\n{comment_body}"
            )
            continue

        comments.append(
            {
                "path": rel_path,
                "body": comment_body,
                "side": "RIGHT",
                "line": end_line,
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

    review = {
        "body": "clang-tidy made some suggestions",
        "event": "COMMENT",
        "comments": comments,
    }
    return review


def load_metadata() -> Metadata:
    """Load metadata from the METADATA_FILE path"""

    with open(METADATA_FILE, "r") as metadata_file:
        x = json.load(metadata_file)
        print(f"x: {x}")
        return x

def save_metadata(pr_number: int) -> None:
    """Save metadata to the METADATA_FILE path"""

    metadata: Metadata = {
            "pr_number": pr_number
            }

    with open(METADATA_FILE, "w") as metadata_file:
        json.dump(metadata, metadata_file)


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
    line_filter, build_dir, clang_tidy_checks, clang_tidy_binary, config_file, files
    ):
    """Run clang-tidy on the given files and save output into FIXES_FILE"""

    if config_file != "":
        config = f'-config-file="{config_file}"'
    else:
        config = f"-checks={clang_tidy_checks}"

    print(f"Using config: {config}")

    command = f"{clang_tidy_binary} -p={build_dir} {config} -line-filter={line_filter} {files} --export-fixes={FIXES_FILE}"

    start = datetime.datetime.now()
    try:
        with message_group(f"Running:\n\t{command}"):
            output = subprocess.run(
                command, capture_output=True, shell=True, check=True, encoding="utf-8"
            )
    except subprocess.CalledProcessError as e:
        print(
            f"\n\nclang-tidy failed with return code {e.returncode} and error:\n{e.stderr}\nOutput was:\n{e.stdout}"
        )
    end = datetime.datetime.now()

    print(f"Took: {end - start}")

    try:
        with open(FIXES_FILE, "r") as fixes_file:
            return yaml.safe_load(fixes_file)
    except FileNotFoundError:
        return {}


def cull_comments(pull_request: PullRequest, review, max_comments):
    """Remove comments from review that have already been posted, and keep
    only the first max_comments

    """

    comments = pull_request.get_pr_comments()

    for comment in comments:
        review["comments"] = list(
            filter(
                lambda review_comment: not (
                    review_comment["path"] == comment["path"]
                    and review_comment["line"] == comment["line"]
                    and review_comment["body"] == comment["body"]
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


def strip_enclosing_quotes(string: str) -> str:
    """Strip leading/trailing whitespace and remove any enclosing quotes"""
    stripped = string.strip()

    # Need to check double quotes again in case they're nested inside
    # single quotes
    for quote in ['"', "'", '"']:
        if stripped.startswith(quote) and stripped.endswith(quote):
            stripped = stripped[1:-1]
    return stripped
