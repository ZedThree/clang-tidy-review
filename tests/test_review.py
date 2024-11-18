import datetime

import clang_tidy_review as ctr

import difflib
import json
import os
import pathlib
import textwrap
import unidiff

import pytest

from pathlib import Path

TEST_DIR = pathlib.Path(__file__).parent
TEST_FILE = TEST_DIR / "src/hello.cxx"
TEST_DIFF = [
    unidiff.PatchSet(
        r"""diff --git a/src/hello.cxx b/src/hello.cxx
index 98edef4..6651631 100644
--- a/src/hello.cxx
+++ b/src/hello.cxx
@@ -2,6 +2,18 @@

 #include  <string>

+const std::string selective_hello(std::string name) {
+  if (name.compare("Peter")) {
+    return "Sorry, I thought you were someone else\n";
+  } else {
+    return "I'm so happy to see you!\n";
+  }
+}
+
+const std::string hello() {
+  return "Hello!\n";
+}
+
 std::string hello(std::string name) {
   using namespace std::string_literals;
   return "Hello "s + name + "!\n"s;
"""
    )[0]
]
TEST_OFFSET_LOOKUP = {
    str(TEST_FILE): [
        0,
        20,
        21,
        40,
        41,
        95,
        126,
        181,
        192,
        233,
        237,
        239,
        240,
        268,
        289,
        291,
        292,
        330,
        370,
        406,
        408,
        409,
        422,
        453,
        455,
    ]
}
TEST_DIAGNOSTIC = {
    "Message": (
        "return type 'const std::string' (aka 'const basic_string<char>') is 'const'-"
        "qualified at the top level, which may reduce code readability without improving "
        "const correctness"
    ),
    "FilePath": str(TEST_FILE),
    "FileOffset": 41,
    "Replacements": [
        {
            "FilePath": str(TEST_FILE),
            "Offset": 41,
            "Length": 6,
            "ReplacementText": "",
        }
    ],
}


class MockClangTidyVersionProcess:
    """Mock out subprocess call to clang-tidy --version"""

    def __init__(self, version: int):
        self.stdout = f"""\
LLVM (http://llvm.org/):
  LLVM version {version}.1.7
  Optimized build.
  Default target: x86_64
  Host CPU: skylake
        """


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


def test_save_load_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(ctr, "METADATA_FILE", tmp_path / ctr.METADATA_FILE)

    ctr.save_metadata(42)
    meta = ctr.load_metadata()

    assert meta["pr_number"] == 42


def make_diff():
    with open(TEST_DIR / "src/hello_original.cxx") as f:
        old = f.read()

    with open(TEST_FILE) as f:
        new = f.read()

    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile="a/src/hello.cxx",
            tofile="b/src/hello.cxx",
            lineterm="",
        )
    )

    diff_cxx = f"diff --git a/src/hello.cxx b/src/hello.cxx\nindex 98edef4..6651631 100644\n{diff}"
    diff_cpp = diff_cxx.replace("cxx", "cpp")
    diff_goodbye = diff_cxx.replace("hello", "goodbye")

    diff_list = [unidiff.PatchSet(f)[0] for f in [diff_cxx, diff_cpp, diff_goodbye]]

    return diff_list


def test_filter_files():
    filtered = ctr.filter_files(make_diff(), ["*.cxx"], ["*goodbye.*"])
    assert filtered == ["src/hello.cxx"]


def test_line_ranges():
    line_ranges = ctr.get_line_ranges(TEST_DIFF, ["src/hello.cxx"])

    expected_line_ranges = '[{"name":"src/hello.cxx","lines":[[5,16]]}]'
    assert line_ranges == expected_line_ranges


def test_load_clang_tidy_warnings():
    warnings = ctr.load_clang_tidy_warnings(TEST_DIR / f"src/test_{ctr.FIXES_FILE}")

    assert sorted(list(warnings.keys())) == ["Diagnostics", "MainSourceFile"]
    assert warnings["MainSourceFile"] == "/clang_tidy_review/src/hello.cxx"
    assert len(warnings["Diagnostics"]) == 7


def test_file_line_lookup():
    line_lookup = ctr.make_file_line_lookup(TEST_DIFF)

    assert line_lookup == {"src/hello.cxx": dict(zip(range(2, 20), range(1, 19)))}


def test_file_offset_lookup():
    offset_lookup = ctr.make_file_offset_lookup([TEST_FILE])

    assert offset_lookup == TEST_OFFSET_LOOKUP


def test_find_linenumber_from_offset():
    line_num = ctr.find_line_number_from_offset(TEST_OFFSET_LOOKUP, TEST_FILE, 42)
    assert line_num == 4


def test_read_one_line():
    line = ctr.read_one_line(TEST_FILE, TEST_OFFSET_LOOKUP[str(TEST_FILE)][4])
    assert line == "const std::string selective_hello(std::string name) {"


def test_format_diff_line():
    source_line = "const std::string selective_hello(std::string name) {"

    code_blocks, end_line = ctr.format_diff_line(
        TEST_DIAGNOSTIC, TEST_OFFSET_LOOKUP, source_line, 0, 4
    )

    expected_replacement = textwrap.dedent(
        """
        ```suggestion
        std::string selective_hello(std::string name) {
        ```
        """
    )
    assert code_blocks == expected_replacement
    assert end_line == 4


def test_make_comment():
    comment, end_line = ctr.make_comment_from_diagnostic(
        "readability-const-return-type",
        TEST_DIAGNOSTIC,
        str(TEST_FILE),
        TEST_OFFSET_LOOKUP,
        [],
    )

    expected_comment = textwrap.dedent(
        """\
        warning: return type 'const std::string' (aka 'const basic_string<char>') is 'const'-qualified at the top level, which may reduce code readability without improving const correctness [readability-const-return-type]

        ```suggestion
        std::string selective_hello(std::string name) {
        ```
        """  # noqa: E501
    )
    assert comment == expected_comment
    assert end_line == 5


def test_format_notes():
    message = ctr.format_notes([], TEST_OFFSET_LOOKUP)
    assert message == ""

    notes = [
        {"Message": "Test message 1", "FilePath": str(TEST_FILE), "FileOffset": 42},
        {"Message": "Test message 2", "FilePath": str(TEST_FILE), "FileOffset": 98},
    ]

    # Make sure we're in the test directory so the relative paths work
    os.chdir(TEST_DIR)
    message = ctr.format_notes(notes, TEST_OFFSET_LOOKUP)

    assert message == textwrap.dedent(
        """\
    <details>
    <summary>Additional context</summary>

    **src/hello.cxx:4:** Test message 1
    ```cpp
    const std::string selective_hello(std::string name) {
     ^
    ```
    **src/hello.cxx:5:** Test message 2
    ```cpp
      if (name.compare("Peter")) {
       ^
    ```

    </details>
        """
    )


def test_make_comment_with_notes():
    comment, end_line = ctr.make_comment_from_diagnostic(
        "readability-const-return-type",
        TEST_DIAGNOSTIC,
        str(TEST_FILE),
        TEST_OFFSET_LOOKUP,
        [
            {"Message": "Test message 1", "FilePath": str(TEST_FILE), "FileOffset": 42},
            {"Message": "Test message 2", "FilePath": str(TEST_FILE), "FileOffset": 98},
        ],
    )

    expected_comment = textwrap.dedent(
        """\
        warning: return type 'const std::string' (aka 'const basic_string<char>') is 'const'-qualified at the top level, which may reduce code readability without improving const correctness [readability-const-return-type]

        ```suggestion
        std::string selective_hello(std::string name) {
        ```
        <details>
        <summary>Additional context</summary>

        **src/hello.cxx:4:** Test message 1
        ```cpp
        const std::string selective_hello(std::string name) {
         ^
        ```
        **src/hello.cxx:5:** Test message 2
        ```cpp
          if (name.compare("Peter")) {
           ^
        ```

        </details>
        """  # noqa: E501
    )
    assert comment == expected_comment
    assert end_line == 5


def test_version(monkeypatch):
    # Mock out the actual call so this test doesn't depend on a
    # particular version of clang-tidy being installed
    expected_version = 42
    monkeypatch.setattr(
        ctr.subprocess,
        "run",
        lambda *args, **kwargs: MockClangTidyVersionProcess(expected_version),
    )

    version = ctr.clang_tidy_version(Path("not-clang-tidy"))
    assert version == expected_version


def test_config_file(monkeypatch, tmp_path):
    # Mock out the actual call so this test doesn't depend on a
    # particular version of clang-tidy being installed
    monkeypatch.setattr(
        ctr.subprocess, "run", lambda *args, **kwargs: MockClangTidyVersionProcess(15)
    )

    config_file = tmp_path / ".clang-tidy"

    # If you set clang_tidy_checks to something and config_file to something, config_file is sent to clang-tidy.
    flag = ctr.config_file_or_checks(
        Path("not-clang-tidy"),
        clang_tidy_checks="readability",
        config_file=str(config_file),
    )
    assert flag == f"--config-file={config_file}"

    # If you set clang_tidy_checks and config_file to an empty string, neither are sent to the clang-tidy.
    flag = ctr.config_file_or_checks(
        Path("not-clang-tidy"), clang_tidy_checks="", config_file=""
    )
    assert flag is None

    # If you get config_file to something, config_file is sent to clang-tidy.
    flag = ctr.config_file_or_checks(
        Path("not-clang-tidy"), clang_tidy_checks="", config_file=str(config_file)
    )
    assert flag == f"--config-file={config_file}"

    # If you get clang_tidy_checks to something and config_file to nothing, clang_tidy_checks is sent to clang-tidy.
    flag = ctr.config_file_or_checks(
        Path("not-clang-tidy"), clang_tidy_checks="readability", config_file=""
    )
    assert flag == "--checks=readability"


def test_decorate_comment_body():
    # No link to generic error so the message shouldn't be changed
    error_message = (
        "warning: no member named 'ranges' in namespace 'std' [clang-diagnostic-error]"
    )
    assert ctr.decorate_check_names(error_message) == error_message

    todo_message = "warning: missing username/bug in TODO [google-readability-todo]"
    todo_message_decorated = "warning: missing username/bug in TODO [[google-readability-todo](https://clang.llvm.org/extra/clang-tidy/checks/google/readability-todo.html)]"
    assert ctr.decorate_check_names(todo_message) == todo_message_decorated

    naming_message = "warning: invalid case style for constexpr variable 'foo' [readability-identifier-naming]"
    naming_message_decorated = "warning: invalid case style for constexpr variable 'foo' [[readability-identifier-naming](https://clang.llvm.org/extra/clang-tidy/checks/readability/identifier-naming.html)]"
    assert ctr.decorate_check_names(naming_message) == naming_message_decorated

    clang_analyzer_message = "warning: Array access (from variable 'foo') results in a null pointer dereference [clang-analyzer-core.NullDereference]"
    clang_analyzer_message_decorated = "warning: Array access (from variable 'foo') results in a null pointer dereference [[clang-analyzer-core.NullDereference](https://clang.llvm.org/extra/clang-tidy/checks/clang-analyzer/core.NullDereference.html)]"
    assert (
        ctr.decorate_check_names(clang_analyzer_message)
        == clang_analyzer_message_decorated
    )

    # Not sure it's necessary to link to prior version documentation especially since we have to map versions such as
    # "17" to "17.0.1" and "18" to "18.1.0" because no other urls exist
    # version_message_pre_15_version = "14.0.0"
    # version_message_pre_15 = "warning: missing username/bug in TODO [google-readability-todo]"
    # version_message_pre_15_decorated = "warning: missing username/bug in TODO [[google-readability-todo](https://releases.llvm.org/14.0.0/tools/clang/tools/extra/docs/clang-tidy/checks/google-readability-todo.html)]"
    # assert ctr.decorate_check_names(version_message_pre_15, version_message_pre_15_version) == version_message_pre_15_decorated
    #
    # version_message_1500_version = "15.0.0"
    # version_message_1500 = "warning: missing username/bug in TODO [google-readability-todo]"
    # version_message_1500_decorated = "warning: missing username/bug in TODO [[google-readability-todo](https://releases.llvm.org/15.0.0/tools/clang/tools/extra/docs/clang-tidy/checks/google/readability-todo.html)]"
    # assert ctr.decorate_check_names(version_message_1500, version_message_1500_version) == version_message_1500_decorated


def test_timing_summary(monkeypatch):
    monkeypatch.setattr(ctr, "PROFILE_DIR", TEST_DIR / "src/clang-tidy-profile")
    profiling = ctr.load_and_merge_profiling()
    assert "time.clang-tidy.total.wall" in profiling["hello.cxx"].keys()
    assert "time.clang-tidy.total.user" in profiling["hello.cxx"].keys()
    assert "time.clang-tidy.total.sys" in profiling["hello.cxx"].keys()
    summary = ctr.make_timing_summary(profiling, datetime.timedelta(seconds=42))
    assert len(summary.split("\n")) == 22
