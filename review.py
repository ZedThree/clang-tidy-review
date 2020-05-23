#!/usr/bin/env python3

import argparse
import unidiff
import itertools
import subprocess
from github import Github
import textwrap
import os


def make_file_line_lookup(diff):
    lookup = {}
    for file in diff:
        filename = file.target_file[2:]
        lookup[filename] = {}
        for hunk in file:
            for line in hunk:
                if line.is_added:
                    lookup[filename][line.target_line_no] = line.diff_line_no - 5
    return lookup


def make_review(contents, lookup):
    root = os.getcwd()
    comments = []
    for num, line in enumerate(contents):
        if "warning" in line:
            full_path, source_line, _, warning = line.split(":", maxsplit=3)
            rel_path = os.path.relpath(full_path, root)
            body = ""
            for line2 in contents[num + 1 :]:
                if "warning" in line2:
                    break
                body += "\n" + line2

            comment_body = f"""{warning.strip().replace("'", "`")}

```cpp
{body.strip()}
```
"""
            comments.append(
                {
                    "path": rel_path,
                    "body": comment_body,
                    "position": lookup[rel_path][int(source_line)],
                }
            )

    review = {
        "body": "clang-tidy made some suggestions",
        "event": "COMMENT",
        "comments": comments,
    }
    return review


def get_PR_diff(owner, repo, pr_number, token):
    status = subprocess.call(
        [
            "curl",
            "-v",
            "-H",
            "Accept: application/vnd.github.v3.diff",
            "-H",
            f"Authorization: token {token}",
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            "-o",
            "tmp.json",
        ]
    )

    with open("tmp.json", "r") as f:
        print(f.read())

    if status != 0:
        raise RuntimeError(f"curl failed with {status}")

    with open("tmp.json", "r") as f:
        contents = f.readlines()
    os.remove("tmp.json")
    return unidiff.PatchSet(contents)


def get_clang_tidy_warnings(
    branch, build_dir, clang_tidy_diff, clang_tidy_checks, clang_tidy_binary
):

    child = subprocess.Popen(
        f"git diff -U0 {branch} | {clang_tidy_diff} -clang-tidy-binary {clang_tidy_binary} -p1 -path={build_dir} -checks={clang_tidy_checks}",
        stdout=subprocess.PIPE,
        shell=True,
    )
    output = child.stdout.read().decode("utf-8", "ignore")

    return output.splitlines()


def main(
    owner,
    repo,
    pr,
    branch,
    build_dir,
    clang_tidy_diff,
    clang_tidy_checks,
    clang_tidy_binary,
    token,
):
    diff = get_PR_diff(owner, repo, pr, token)
    print(diff)
    lookup = make_file_line_lookup(diff)
    print(lookup)

    clang_tidy_warnings = get_clang_tidy_warnings(
        branch, build_dir, clang_tidy_diff, clang_tidy_checks, clang_tidy_binary
    )

    review = make_review(clang_tidy_warnings, lookup)

    print(review)
    g = Github(token)
    repo = g.get_repo(f"{owner}/{repo}")
    print(repo)
    pull_request = repo.get_pull(pr)
    print(pull_request)
    return pull_request.create_review(**review)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a review from clang-tidy warnings"
    )
    parser.add_argument("--owner", help="Repo owner")
    parser.add_argument("--repo", help="repo name")
    parser.add_argument("--pr", help="PR number", type=int)
    parser.add_argument("--branch", help="merge base")
    parser.add_argument(
        "--clang_tidy_diff",
        help="clang-tidy-diff binary",
        default="/usr/bin/clang-tidy-diff-9.py",
    )
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
    parser.add_argument("--token", help="github auth token")

    args = parser.parse_args()

    exit(
        main(
            owner=args.owner,
            repo=args.repo,
            pr=args.pr,
            branch=args.branch,
            build_dir=args.build_dir,
            clang_tidy_diff=args.clang_tidy_diff,
            clang_tidy_checks=args.clang_tidy_checks,
            clang_tidy_binary=args.clang_tidy_binary,
            token=args.token,
        )
    )
