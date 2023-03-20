import clang_tidy_review as ctr

import difflib
import json
import os
import pathlib
import textwrap
import unidiff

import pytest

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
    monkeypatch.setattr(ctr, "METADATA_FILE", str(tmp_path / ctr.METADATA_FILE))

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

    expected_line_ranges = """["{'name': 'src/hello.cxx', 'lines': [[5, 16]]}"]"""
    assert line_ranges == expected_line_ranges


def test_load_clang_tidy_warnings(monkeypatch):
    monkeypatch.setattr(ctr, "FIXES_FILE", str(TEST_DIR / f"src/test_{ctr.FIXES_FILE}"))

    warnings = ctr.load_clang_tidy_warnings()

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

    version = ctr.clang_tidy_version("not-clang-tidy")
    assert version == expected_version


def test_config_file(monkeypatch, tmp_path):
    # Mock out the actual call so this test doesn't depend on a
    # particular version of clang-tidy being installed
    monkeypatch.setattr(
        ctr.subprocess, "run", lambda *args, **kwargs: MockClangTidyVersionProcess(12)
    )

    config_file = tmp_path / ".clang-tidy"
    config_file.touch()

    flag = ctr.config_file_or_checks("not-clang-tidy", "readability", str(config_file))
    assert flag == f'--config-file="{config_file}"'

    os.chdir(tmp_path)
    flag = ctr.config_file_or_checks("not-clang-tidy", "readability", "")
    assert flag == '--config-file=".clang-tidy"'

    monkeypatch.setattr(
        ctr.subprocess, "run", lambda *args, **kwargs: MockClangTidyVersionProcess(11)
    )
    flag = ctr.config_file_or_checks("not-clang-tidy", "readability", "")
    assert flag == "--config"

    config_file.unlink()

    flag = ctr.config_file_or_checks("not-clang-tidy", "readability", "")
    assert flag == "--checks=readability"
