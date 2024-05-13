# Clang-Tidy Review

Create a pull-request review based on the warnings from clang-tidy.

Inspired by `clang-tidy-diff`, Clang-Tidy Review only runs on the
changes in the pull request. This makes it nice and speedy, as well as
being useful for projects that aren't completely clang-tidy clean yet.

Where possible, makes the warnings into suggestions so you can apply
them immediately.

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
    - uses: actions/checkout@v4

    # Optionally generate compile_commands.json

    - uses: ZedThree/clang-tidy-review@v0.14.0
      id: review

    # Uploads an artefact containing clang_fixes.json
    - uses: ZedThree/clang-tidy-review/upload@v0.14.0
      id: upload-review

    # If there are any comments, fail the check
    - if: steps.review.outputs.total_comments > 0
      run: exit 1
```

The `ZedThree/clang-tidy-review/upload` Action is optional (unless using the
split workflow, see below), and will upload some of the output files as workflow
artefacts. These are useful when there are more comments than can be posted, as
well as for applying fixes locally.

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
- `base_dir`: Absolute path to initial working directory
  `GITHUB_WORKSPACE`.
  - default: `GITHUB_WORKSPACE`
- `clang_tidy_version`: Version of clang-tidy to use; one of 14, 15, 16, 17, 18
  - default: '18'
- `clang_tidy_checks`: List of checks
  - default: `'-*,performance-*,readability-*,bugprone-*,clang-analyzer-*,cppcoreguidelines-*,mpi-*,misc-*'`
- `config_file`: Path to clang-tidy config file, replaces `clang_tidy_checks`
  - default: '' which will use `clang_tidy_checks` if there are any, else closest `.clang-tidy` to each file
- `include`: Comma-separated list of files or patterns to include
  - default: `"*.[ch],*.[ch]xx,*.[ch]pp,*.[ch]++,*.cc,*.hh"`
- `exclude`: Comma-separated list of files or patterns to exclude
  - default: ''
- `apt_packages`: Comma-separated list of apt packages to install
  - default: ''
- `cmake_command`: A CMake command to configure your project and generate
  `compile_commands.json` in `build_dir`. You _almost certainly_ want
  to include `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`!
  - default: ''
- `max_comments`: Maximum number of comments to post at once
  - default: '25'
- `lgtm_comment_body`: Message to post on PR if no issues are
  found. An empty string will post no LGTM comment.
  - default: 'clang-tidy review says "All clean, LGTM! :+1:"'
- `split_workflow`: Only generate but don't post the review, leaving
  it for the second workflow. Relevant when receiving PRs from forks
  that don't have the required permissions to post reviews.
  - default: false
- `annotations`: Use Annotations instead of comments. A maximum of 10
  annotations can be written fully, the rest will be summarised. This is a
  limitation of the GitHub API.
- `num_comments_as_exitcode`: Set the exit code to be the amount of comments (enabled by default).

## Outputs

- `total_comments`: Total number of warnings from clang-tidy

## Generating `compile_commands.json`

Very simple projects can get away without a `compile_commands.json`
file, but for most projects `clang-tidy` needs this file in order to
find include paths and macro definitions.

If you use the GitHub `ubuntu-latest` image as your normal `runs-on`
container, you only install packages from the system package manager,
and don't need to build or install other tools yourself, then you can
generate `compile_commands.json` as part of the `clang-tidy-review`
action:

```yaml
name: clang-tidy-review
on: [pull_request]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v4

    - uses: ZedThree/clang-tidy-review@v0.14.0
      id: review
      with:
        # List of packages to install
        apt_packages: liblapack-dev
        # CMake command to run in order to generate compile_commands.json
        cmake_command: cmake . -DCMAKE_EXPORT_COMPILE_COMMANDS=on
```

If you don't use CMake, this may still work for you if you can use a
tool like [bear](https://github.com/rizsotto/Bear) for example.

You can also generate this file outside the container, e.g. by adding
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` to a cmake command in an earlier
action and omitting the `cmake_command` paramter.

## Use in a non-default location

If you're using the `container` argument in your GitHub workflow,
downloading/building other tools manually, or not using CMake, you
will need to generate `compile_commands.json` before the
`clang-tidy-review` action. However, the Action is run inside another
container, and due to the way GitHub Actions work, `clang-tidy-review`
ends up running with a different absolute path.

What this means is that if `compile_commands.json` contains absolute
paths, `clang-tidy-review` needs to adjust them to where it is being
run instead. By default, it replaces absolute paths that start with
the value of [`${GITHUB_WORKSPACE}`][env_vars] with the new working
directory.

If you're running in a container other than a default GitHub
container, then you may need to pass the working directory to
`base_dir`. Unfortunately there's not an easy way for
`clang-tidy-review` to auto-detect this, so in order to pass the
current directory you will need to do something like the following:

```yaml
name: clang-tidy-review
on: [pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    # Using another container changes the
    # working directory from GITHUB_WORKSPACE
    container:
      image: my-container

    steps:
    - uses: actions/checkout@v4

    # Get the current working directory and set it
    # as an environment variable
    - name: Set base_dir
      run: echo "base_dir=$(pwd)" >> $GITHUB_ENV

    - uses: ZedThree/clang-tidy-review@v0.14.0
      id: review
      with:
        # Tell clang-tidy-review the base directory.
        # This will get replaced by the new working
        # directory inside the action
        base_dir: ${{ env.base_dir }}
```

## Usage in fork environments (Split workflow)

Actions from forks are limited in their permissions for your security. To
support this use case, you can use the split workflow described below.

Example review workflow:

```yaml
name: clang-tidy-review

# You can be more specific, but it currently only works on pull requests
on: [pull_request]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v4

    # Optionally generate compile_commands.json

    - uses: ZedThree/clang-tidy-review@v0.14.0
      with:
        split_workflow: true

    - uses: ZedThree/clang-tidy-review/upload@v0.14.0
```
The `clang-tidy-review/upload` Action will automatically upload the following
files as workflow artefacts:

- `clang-tidy-review-output.json`
- `clang-tidy-review-metadata.json`
- `clang_fixes.json`

Example post comments workflow:

```yaml
name: Post clang-tidy review comments

on:
  workflow_run:
    # The name field of the lint action
    workflows: ["clang-tidy-review"]
    types:
      - completed

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - uses: ZedThree/clang-tidy-review/post@v0.14.0
        # lgtm_comment_body, max_comments, and annotations need to be set on the posting workflow in a split setup
        with:
          # adjust options as necessary
          lgtm_comment_body: ''
          annotations: false
          max_comments: 10
```

This Action will try to automatically download
`clang-tidy-review-{output,metadata}.json` from the workflow that triggered it.

The review workflow runs with limited permissions and no access to
repo/organisation secrets, while the post comments workflow has the required
permissions because it's triggered by the `workflow_run` event and always uses
the version of the workflow in the original repo.

Read more about workflow security limitations
[here](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/).

Ensure that your workflow name doesn't contain any [special characters](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet) as Github [does not treat](https://github.com/orgs/community/discussions/50835#discussioncomment-5428789) `on.workflow_run.workflows` literally.

## Project layout

This project is laid out as follows:

```
.
├── action.yml       # The `review` Action
├── Dockerfile
└── post
    ├── action.yml   # The `post` Action
    ├── Dockerfile
    └── clang_tidy_review      # Common python package
        └── clang_tidy_review
            ├── __init__.py
            ├── post.py        # Entry point for `post`
            └── review.py      # Entry point for `review`
```

In order to accommodate the split workflow, the `review` and `post`
actions must have their own Action metadata files. GitHub requires
this file to be named exactly `action.yml`, so they have to be in
separate directories. The associated `Dockerfile`s must also be named
exactly `Dockerfile`, so they also have to be separate directories.

Lastly, we want to be able to reuse the python package between the two
Actions, which means it must be in a subdirectory of _both_
`Dockerfile`s because they can't see parent directories.

Which is why we've ended up with this slightly strange structure! This
way, we can `COPY` the python package into both Docker images.


## Real world project samples
|Project|Workflow|
|----------|-------|
|[BOUT++](https://github.com/boutproject/BOUT-dev) |[CMake](https://github.com/boutproject/BOUT-dev/blob/master/.github/workflows/clang-tidy-review.yml) |
|[Mudlet](https://github.com/Mudlet/Mudlet) |[CMake + Qt](https://github.com/Mudlet/Mudlet/blob/development/.github/workflows/clangtidy-diff-analysis.yml) |



[env_vars]: https://docs.github.com/en/actions/learn-github-actions/environment-variables#default-environment-variables
