import clang_tidy_review as ctr

import json
import os
import pathlib
import textwrap

import pytest


def test_message_group(capsys):
    with ctr.message_group("some_title"):
        print("message body")

    captured = capsys.readouterr()

    assert captured.out == "::group::some_title\nmessage body\n::endgroup::\n"


def test_set_output(tmp_path):
    test_output = tmp_path / "test_output.txt"
    os.environ["GITHUB_OUTPUT"] = str(test_output)

    ctr.set_output("key", "value")

    with open(test_output) as f:
        contents = f.read()

    assert contents.strip() == "key=value"


def test_format_ordinary_line():
    text = ctr.format_ordinary_line("123456", 4)

    assert text == textwrap.dedent(
        """\
        ```cpp
        123456
            ^
        ```
        """
    )


@pytest.mark.parametrize(
    ("in_text", "expected_text"),
    [
        (""" 'some text' """, "some text"),
        (""" "some text" """, "some text"),
        (""" "some 'text'" """, "some 'text'"),
        (""" 'some "text"' """, 'some "text"'),
        (""" "'some text'" """, "some text"),
        (""" '"some 'text'"' """, "some 'text'"),
    ],
)
def test_strip_enclosing_quotes(in_text, expected_text):
    assert ctr.strip_enclosing_quotes(in_text) == expected_text


def test_try_relative():
    here = pathlib.Path.cwd()

    path = ctr.try_relative(".")
    assert path == here

    path = ctr.try_relative(here / "..")
    assert path == pathlib.Path("..")

    path = ctr.try_relative("/fake/path")
    assert path == pathlib.Path("/fake/path")


def test_fix_absolute_paths(tmp_path):
    compile_commands = """
[
{
  "directory": "/fake/path/to/project/build",
  "command": "/usr/bin/c++    -o CMakeFiles/hello.dir/src/hello.cxx.o -c /fake/path/to/project/src/hello.cxx",
  "file": "/fake/path/to/project/src/hello.cxx"
}
]
    """

    compile_commands_path = tmp_path / "compile_commands.json"
    with open(compile_commands_path, "w") as f:
        f.write(compile_commands)

    ctr.fix_absolute_paths(compile_commands_path, "/fake/path/to/project")

    with open(compile_commands_path, "r") as f:
        contents = json.load(f)[0]

    here = pathlib.Path.cwd()
    assert contents["directory"] == str(here / "build")
    assert contents["command"].split()[-1] == str(here / "src/hello.cxx")
    assert contents["file"] == str(here / "src/hello.cxx")
