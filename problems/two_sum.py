"""Two Sum problem definition."""

from syscall.models import Problem, TestCase

problem = Problem(
    id="two_sum",
    title="Two Sum",
    description=(
        "Given an array of integers `nums` and an integer `target`, return the "
        "two indices (0-based) of the numbers that add up to `target`, sorted in "
        "ascending order. You may assume each input has exactly one solution, and "
        "you may not use the same element twice."
    ),
    function_signature="def solve(nums: list[int], target: int) -> list[int]:",
    test_cases=[
        TestCase(input='{"args": [[2, 7, 11, 15], 9]}', expected_output="[0, 1]"),
        TestCase(input='{"args": [[3, 2, 4], 6]}', expected_output="[1, 2]"),
        TestCase(input='{"args": [[3, 3], 6]}', expected_output="[0, 1]"),
    ],
    hidden_test_cases=[
        # Large input — O(n^2) will be noticeably slower
        # Array: [0, 1, 2, ..., 99999], target=199997 → indices [99998, 99999]
        TestCase(
            input='{"args": [%s, 199997]}' % (
                "[" + ",".join(str(i) for i in range(100_000)) + "]"
            ),
            expected_output="[99998, 99999]",
        ),
        # target=149999 → indices [74999, 75000] since 74999 + 75000 = 149999
        TestCase(
            input='{"args": [%s, 149999]}' % (
                "[" + ",".join(str(i) for i in range(100_000)) + "]"
            ),
            expected_output="[74999, 75000]",
        ),
    ],
    timeout_seconds=10.0,
)
