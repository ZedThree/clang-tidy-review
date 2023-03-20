# clang-tidy review
# Copyright (c) 2020 Peter Hill
# SPDX-License-Identifier: MIT
# See LICENSE for more information

import fnmatch
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
import re
import io
import zipfile
from github import Github
from github.Requester import Requester
from github.PaginatedList import PaginatedList
from github.WorkflowRun import WorkflowRun
from typing import List, Optional, TypedDict

DIFF_HEADER_LINE_LENGTH = 5
FIXES_FILE = "clang_tidy_review.yaml"
METADATA_FILE = "clang-tidy-review-metadata.json"
REVIEW_FILE = "clang-tidy-review-output.json"
MAX_ANNOTATIONS = 10


class Metadata(TypedDict):
    """Loaded from `METADATA_FILE`
    Contains information necessary to post a review without pull request knowledge

    """

    pr_number: int


class PRReviewComment(TypedDict):
    path: str
    position: Optional[int]
    body: str
    line: Optional[int]
    side: Optional[str]
    start_line: Optional[int]
    start_side: Optional[str]


class PRReview(TypedDict):
    body: str
    event: str
    comments: List[PRReviewComment]


def build_clang_tidy_warnings(
    line_filter, build_dir, clang_tidy_checks, clang_tidy_binary, config_file, files
) -> None:
    """Run clang-tidy on the given files and save output into FIXES_FILE"""

    config = config_file_or_checks(clang_tidy_binary, clang_tidy_checks, config_file)

    print(f"Using config: {config}")

    command = f"{clang_tidy_binary} -p={build_dir} {config} -line-filter={line_filter} {files} --export-fixes={FIXES_FILE}"

    start = datetime.datetime.now()
    try:
        with message_group(f"Running:\n\t{command}"):
            subprocess.run(
                command, capture_output=True, shell=True, check=True, encoding="utf-8"
            )
    except subprocess.CalledProcessError as e:
        print(
            f"\n\nclang-tidy failed with return code {e.returncode} and error:\n{e.stderr}\nOutput was:\n{e.stdout}"
        )
    end = datetime.datetime.now()

    print(f"Took: {end - start}")


def clang_tidy_version(clang_tidy_binary: str):
    try:
        version_out = subprocess.run(
            f"{clang_tidy_binary} --version",
            capture_output=True,
            shell=True,
            check=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        print(f"\n\nWARNING: Couldn't get clang-tidy version, error was: {e}")
        return 0

    if version := re.search(r"version (\d+)", version_out):
        return int(version.group(1))

    print(
        f"\n\nWARNING: Couldn't get clang-tidy version number, '{clang_tidy_binary} --version' reported: {version_out}"
    )
    return 0


def config_file_or_checks(
    clang_tidy_binary: str, clang_tidy_checks: str, config_file: str
):
    version = clang_tidy_version(clang_tidy_binary)

    # If config_file is set, use that
    if config_file == "":
        if pathlib.Path(".clang-tidy").exists():
            config_file = ".clang-tidy"
    elif not pathlib.Path(config_file).exists():
        print(f"WARNING: Could not find specified config file '{config_file}'")
        config_file = ""

    if not config_file:
        return f"--checks={clang_tidy_checks}"

    if version >= 12:
        return f'--config-file="{config_file}"'

    if config_file != ".clang-tidy":
        print(
            f"\n\nWARNING: non-default config file name '{config_file}' will be ignored for "
            "selected clang-tidy version {version}. This version expects exactly '.clang-tidy'\n"
        )

    return "--config"


def load_clang_tidy_warnings():
    """Read clang-tidy warnings from FIXES_FILE. Can be produced by build_clang_tidy_warnings"""
    try:
        with open(FIXES_FILE, "r") as fixes_file:
            return yaml.safe_load(fixes_file)
    except FileNotFoundError:
        return {}


class PullRequest:
    """Add some convenience functions not in PyGithub"""

    def __init__(self, repo: str, pr_number: Optional[int], token: str) -> None:
        self.repo_name = repo
        self.pr_number = pr_number
        self.token = token

        # Choose API URL, default to public GitHub
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")

        github = Github(token)
        self.repo = github.get_repo(f"{repo}")
        self._pull_request = None

    @property
    def pull_request(self):
        if self._pull_request is None:
            if self.pr_number is None:
                raise RuntimeError("Missing PR number")

            self._pull_request = self.repo.get_pull(int(self.pr_number))
        return self._pull_request

    def headers(self, media_type: str):
        return {
            "Accept": f"application/vnd.github.{media_type}",
            "Authorization": f"token {self.token}",
        }

    @property
    def base_url(self):
        return f"{self.api_url}/repos/{self.repo_name}/pulls/{self.pr_number}"

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
            self.pull_request._requester,
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

        self.pull_request.create_issue_comment(body)

    def post_review(self, review):
        """Submit a completed review"""
        headers = {
            "Accept": "application/vnd.github.comfort-fade-preview+json",
            "Authorization": f"token {self.token}",
        }
        url = f"{self.base_url}/reviews"

        post_review_response = requests.post(url, json=review, headers=headers)
        print(post_review_response.text)
        try:
            post_review_response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                print(
                    "::error title=Missing permissions::This workflow does not have "
                    "enough permissions to submit a review. This could be because "
                    "the GitHub token specified for this workflow is invalid or "
                    "missing permissions, or it could be because this pull request "
                    "comes from a fork which reduces the default token permissions. "
                    "To support forked workflows, see the "
                    "https://github.com/ZedThree/clang-tidy-review#usage-in-fork-environments "
                    "instructions"
                )

            # Re-raise the exception, causing an error in the workflow
            raise e

    def post_annotations(self, review):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.api_url}/repos/{self.repo_name}/check-runs"

        response = requests.post(url, json=review, headers=headers)
        response.raise_for_status()


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
        # Sometimes, the FilePath may include ".." in "." as a path component
        # However, file paths are stored in the offset table only after being
        # converted to an abs path, in which case the stored path will differ
        # from the FilePath and we'll end up looking for a path that's not in
        # the lookup dict
        # To fix this, we'll convert all the FilePaths to absolute paths
        replacement["FilePath"] = os.path.abspath(replacement["FilePath"])

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

    if notes:
        code_blocks = f"<details>\n<summary>Additional context</summary>\n\n{code_blocks}\n</details>\n"

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


def create_review_file(
    clang_tidy_warnings, diff_lookup, offset_lookup, build_dir
) -> Optional[PRReview]:
    """Create a Github review from a set of clang-tidy diagnostics"""

    if "Diagnostics" not in clang_tidy_warnings:
        return None

    comments: List[PRReviewComment] = []

    for diagnostic in clang_tidy_warnings["Diagnostics"]:
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

    review: PRReview = {
        "body": "clang-tidy made some suggestions",
        "event": "COMMENT",
        "comments": comments,
    }
    return review


def filter_files(diff, include: List[str], exclude: List[str]) -> List:
    changed_files = [filename.target_file[2:] for filename in diff]
    files = []
    for pattern in include:
        files.extend(fnmatch.filter(changed_files, pattern))
        print(f"include: {pattern}, file list now: {files}")
    for pattern in exclude:
        files = [f for f in files if not fnmatch.fnmatch(f, pattern)]
        print(f"exclude: {pattern}, file list now: {files}")

    return files


def create_review(
    pull_request: PullRequest,
    build_dir: str,
    clang_tidy_checks: str,
    clang_tidy_binary: str,
    config_file: str,
    include: List[str],
    exclude: List[str],
) -> Optional[PRReview]:
    """Given the parameters, runs clang-tidy and creates a review.
    If no files were changed, or no warnings could be found, None will be returned.

    """

    diff = pull_request.get_pr_diff()
    print(f"\nDiff from GitHub PR:\n{diff}\n")

    files = filter_files(diff, include, exclude)

    if files == []:
        print("No files to check!")
        return None

    print(f"Checking these files: {files}", flush=True)

    line_ranges = get_line_ranges(diff, files)
    if line_ranges == "[]":
        print("No lines added in this PR!")
        return None

    print(f"Line filter for clang-tidy:\n{line_ranges}\n")

    # Run clang-tidy with the configured parameters and produce the CLANG_TIDY_FIXES file
    build_clang_tidy_warnings(
        line_ranges,
        build_dir,
        clang_tidy_checks,
        clang_tidy_binary,
        config_file,
        '"' + '" "'.join(files) + '"',
    )

    # Read and parse the CLANG_TIDY_FIXES file
    clang_tidy_warnings = load_clang_tidy_warnings()

    print("clang-tidy had the following warnings:\n", clang_tidy_warnings, flush=True)

    diff_lookup = make_file_line_lookup(diff)
    offset_lookup = make_file_offset_lookup(files)

    with message_group("Creating review from warnings"):
        review = create_review_file(
            clang_tidy_warnings, diff_lookup, offset_lookup, build_dir
        )
        with open(REVIEW_FILE, "w") as review_file:
            json.dump(review, review_file)

        return review


def download_artifacts(pull: PullRequest, workflow_id: int):
    """Attempt to automatically download the artifacts from a previous
    run of the review Action"""

    # workflow id is an input: ${{github.event.workflow_run.id }}
    workflow: WorkflowRun = pull.repo.get_workflow_run(workflow_id)
    # I don't understand why mypy complains about the next line!
    for artifact in workflow.get_artifacts():
        if artifact.name == "clang-tidy-review":
            break
    else:
        # Didn't find the artefact, so bail
        print(
            f"Couldn't find 'clang-tidy-review' artifact for workflow '{workflow_id}'. "
            "Available artifacts are: {list(workflow.get_artifacts())}"
        )
        return None, None

    r = requests.get(artifact.archive_download_url, headers=pull.headers("json"))
    if not r.ok:
        print(
            f"WARNING: Couldn't automatically download artifacts for workflow '{workflow_id}', response was: {r}: {r.reason}"
        )
        return None, None

    contents = b"".join(r.iter_content())

    data = zipfile.ZipFile(io.BytesIO(contents))
    filenames = data.namelist()

    metadata = (
        json.loads(data.read(METADATA_FILE)) if METADATA_FILE in filenames else None
    )
    review = json.loads(data.read(REVIEW_FILE)) if REVIEW_FILE in filenames else None
    return metadata, review


def load_metadata() -> Optional[Metadata]:
    """Load metadata from the METADATA_FILE path"""

    if not pathlib.Path(METADATA_FILE).exists():
        print(f"WARNING: Could not find metadata file ('{METADATA_FILE}')", flush=True)
        return None

    with open(METADATA_FILE, "r") as metadata_file:
        return json.load(metadata_file)


def save_metadata(pr_number: int) -> None:
    """Save metadata to the METADATA_FILE path"""

    metadata: Metadata = {"pr_number": pr_number}

    with open(METADATA_FILE, "w") as metadata_file:
        json.dump(metadata, metadata_file)


def load_review() -> Optional[PRReview]:
    """Load review output from the standard REVIEW_FILE path.
    This file contains

    """

    if not pathlib.Path(REVIEW_FILE).exists():
        print(f"WARNING: Could not find review file ('{REVIEW_FILE}')", flush=True)
        return None

    with open(REVIEW_FILE, "r") as review_file:
        payload = json.load(review_file)
        return payload or None


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


def set_output(key: str, val: str) -> bool:
    if "GITHUB_OUTPUT" not in os.environ:
        return False

    # append key-val pair to file
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"{key}={val}\n")

    return True


def post_review(
    pull_request: PullRequest,
    review: Optional[PRReview],
    max_comments: int,
    lgtm_comment_body: str,
    dry_run: bool,
) -> int:
    print(
        "Created the following review:\n", pprint.pformat(review, width=130), flush=True
    )

    if not review or review["comments"] == []:
        print("No warnings to report, LGTM!")
        if not dry_run:
            pull_request.post_lgtm_comment(lgtm_comment_body)
        return 0

    total_comments = len(review["comments"])

    set_output("total_comments", total_comments)

    print("Removing already posted or extra comments", flush=True)
    trimmed_review = cull_comments(pull_request, review, max_comments)

    if trimmed_review["comments"] == []:
        print("Everything already posted!")
        return total_comments

    if dry_run:
        pprint.pprint(review, width=130)
        return total_comments

    print("Posting the review:\n", pprint.pformat(trimmed_review), flush=True)
    pull_request.post_review(trimmed_review)

    return total_comments


def convert_comment_to_annotations(comment):
    return {
        "path": comment["path"],
        "start_line": comment.get("start_line", comment["line"]),
        "end_line": comment["line"],
        "annotation_level": "warning",
        "title": "clang-tidy",
        "message": comment["body"],
    }


def post_annotations(pull_request: PullRequest, review: Optional[PRReview]):
    """Post the first 10 comments in the review as annotations"""

    body = {
        "name": "clang-tidy-review",
        "head_sha": pull_request.pull_request.head.sha,
        "status": "completed",
        "conclusion": "success",
    }

    if review is None:
        return

    if review["comments"] == []:
        print("No warnings to report, LGTM!")
        pull_request.post_annotations(body)

    comments = []
    for comment in review["comments"]:
        first_line = comment["body"].splitlines()[0]
        comments.append(
            f"{comment['path']}:{comment.get('start_line', comment['line'])}: {first_line}"
        )

    total_comments = len(review["comments"])

    body["conclusion"] = "neutral"
    body["output"] = {
        "title": "clang-tidy-review",
        "summary": f"There were {total_comments} warnings",
        "text": "\n".join(comments),
        "annotations": [
            convert_comment_to_annotations(comment)
            for comment in review["comments"][:MAX_ANNOTATIONS]
        ],
    }

    pull_request.post_annotations(body)


def bool_argument(user_input) -> bool:
    """Convert text to bool"""
    user_input = str(user_input).upper()
    if user_input == "TRUE":
        return True
    if user_input == "FALSE":
        return False
    raise ValueError("Invalid value passed to bool_argument")
