# clang-tidy review

Create a pull-request review based on the warnings from clang-tidy.

Doesn't spam by repeating identical warnings for the same line.

![Example review](example_review.png)

Example usage:

```yaml
name: clang-tidy-review

on: [pull_request]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2
    - uses: ZedThree/clang-tidy-review@v0.1.0
      id: review
    - if: steps.review.outpus.total_comments > 0
      run: exit 1
```

## Inputs

- `token`: Authentication token
  - default: `${{ github.token }}`
- `build_dir`: Directory containing the `compile_commands.json` file
  - default: `'.'`
- `clang_tidy_version`: Version of clang-tidy to use; one of 6.0, 7, 8, 9, 10
  - default: '10'
- `clang_tidy_checks`: List of checks
  - default: `'-*,performance-*,readability-*,bugprone-*,clang-analyzer-*,cppcoreguidelines-*,mpi-*,misc-*'`
- `include`: Comma-separated list of files or patterns to include
  - default: `"*.[ch],*.[ch]xx,*.[ch]pp,*.[ch]++,*.cc,*.hh"`
- `exclude`: Comma-separated list of files or patterns to exclude
  - default: ''

## Outputs:

- `total_comments`: Total number of warnings from clang-tidy
