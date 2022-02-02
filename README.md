# Clang-Tidy Review

Create a pull-request review based on the warnings from clang-tidy.

Inspired by `clang-tidy-diff`, Clang-Tidy Review only runs on the
changes in the pull request. This makes it nice and speedy, as well as
being useful for projects that aren't completely clang-tidy clean yet.

Returns the number of comments, so you can decide whether the warnings
act as suggestions, or check failure.

Doesn't spam by repeating identical warnings for the same line.

Can use `compile_commands.json`, so you can optionally configure the
build how you like first.

![Example review](example_review.png)

Example usage:

```yaml
name: clang-tidy-review

# You can be more specific, but it currently only works on pull requests
on: [pull_request]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2

    # Optionally generate compile_commands.json

    - uses: ZedThree/clang-tidy-review@v0.7.0
      id: review
    # If there are any comments, fail the check
    - if: steps.review.outputs.total_comments > 0
      run: exit 1
```

## Limitations

This is a Docker container-based Action because it needs to install
some system packages (the different `clang-tidy` versions) as well as
some Python packages. This that means that there's a two-three minutes
start-up in order to build the Docker container. If you need to
install some additional packages you can pass them via the
`apt_packages` argument.

Except for very simple projects, a `compile_commands.json` file is necessary for
clang-tidy to find headers, set preprocessor macros, and so on. You can generate
one as part of this Action by setting `cmake_command` to something like `cmake
. -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=on`.

GitHub only mounts the `GITHUB_WORKSPACE` directory (that is, the
default place where it clones your repository) on the container. If
you install additional libraries/packages yourself, you'll need to
make sure they are in this directory, otherwise they won't be
accessible from inside this container.

It seems the GitHub API might only accept a limited number of comments
at once, so `clang-tidy-review` will only attempt to post the first
`max_comments` of them (default 25, as this has worked for me).

## Inputs

- `token`: Authentication token
  - default: `${{ github.token }}`
- `build_dir`: Directory containing the `compile_commands.json` file. This
  should be relative to `GITHUB_WORKSPACE` (the default place where your
  repository is cloned)
  - default: `'.'`
- `clang_tidy_version`: Version of clang-tidy to use; one of 6.0, 7, 8, 9, 10, 11
  - default: '11'
- `clang_tidy_checks`: List of checks
  - default: `'-*,performance-*,readability-*,bugprone-*,clang-analyzer-*,cppcoreguidelines-*,mpi-*,misc-*'`
- `config_file`: Path to clang-tidy config file. If set, the config file is used
  instead of `clang_tidy_checks`
  - default: ''
- `include`: Comma-separated list of files or patterns to include
  - default: `"*.[ch],*.[ch]xx,*.[ch]pp,*.[ch]++,*.cc,*.hh"`
- `exclude`: Comma-separated list of files or patterns to exclude
  - default: ''
- `apt_packages`: Comma-separated list of apt packages to install
  - default: ''
- `cmake_command`: A CMake command to configure your project and generate
  `compile_commands.json` in `build_dir`
  - default: ''
- `max_comments`: Maximum number of comments to post at once
  - default: '25'

## Outputs

- `total_comments`: Total number of warnings from clang-tidy

## Real world project samples
|Project|Workflow|
|----------|-------|
|[BOUT++](https://github.com/boutproject/BOUT-dev) |[CMake](https://github.com/boutproject/BOUT-dev/blob/master/.github/workflows/clang-tidy-review.yml) |
|[Mudlet](https://github.com/Mudlet/Mudlet) |[CMake + Qt](https://github.com/Mudlet/Mudlet/blob/development/.github/workflows/clangtidy-diff-analysis.yml) |
