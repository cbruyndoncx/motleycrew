import shutil
from typing import Optional

import os
import argparse
from pathlib import Path
import traceback
import difflib
import json
from copy import copy
from functools import partial

from dotenv import load_dotenv
import nbformat
from nbconvert.preprocessors import ExecutePreprocessor

from motleycrew.common.exceptions import IntegrationTestException
from motleycrew.common import logger, configure_logging

from motleycache import (
    enable_cache,
    set_cache_location,
    set_strong_cache,
)

from examples.old.delegation_crewai import main as delegation_crewai_main
from examples.old.single_llama_index import main as single_llama_index_main


INTEGRATION_TESTS = {
    "single_llama_index": single_llama_index_main,
    "delegation_crewai": delegation_crewai_main,
}

IPYNB_INTEGRATION_TESTS = {
    # "delegation_crewai_ipynb": "examples/delegation_crewai.ipynb",
    # "image_generation_crewai_ipynb": "examples/image_generation_crewai.ipynb",
    # "math_crewai_ipynb": "examples/math_crewai.ipynb",
    # "single_crewai_ipynb": "examples/single_crewai.ipynb",
    # "single_llama_index_ipynb": "examples/single_llama_index.ipynb",
    # "single_openai_tools_react_ipynb": "examples/single_openai_tools_react.ipynb",
}

MINIMAL_INTEGRATION_TESTS = {}

MINIMAL_IPYNB_INTEGRATION_TESTS = {
    # "delegation_crewai_ipynb": "examples/delegation_crewai.ipynb",
    # "image_generation_crewai_ipynb": "examples/image_generation_crewai.ipynb",
    # "math_crewai_ipynb": "examples/math_crewai.ipynb",
    # "single_crewai_ipynb": "examples/single_crewai.ipynb",
    # "single_llama_index_ipynb": "examples/single_llama_index.ipynb",
    # "single_openai_tools_react_ipynb": "examples/single_openai_tools_react.ipynb",
}

DEFAULT_CACHE_DIR = Path(__file__).parent / "itest_cache"
DEFAULT_GOLDEN_DIR = Path(__file__).parent / "itest_golden_data"
TIKTOKEN_CACHE_DIR_NAME = "tiktoken_cache"
TIKTOKEN_CACHE_DIR_ENV_VAR = "TIKTOKEN_CACHE_DIR"


def get_args_parser():
    """Argument parser"""
    parser = argparse.ArgumentParser(
        description="Run integration tests", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--test-name",
        type=str,
        choices=INTEGRATION_TESTS.keys(),
        help="Name of the test to run (leave empty to run all tests)",
        default=None,
    )
    parser.add_argument("--cache-dir", type=str, help="Cache directory", default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--golden-dir", type=str, help="Reference data directory", default=DEFAULT_GOLDEN_DIR
    )
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="Update reference data together with the cache",
    )
    parser.add_argument(
        "--minimal-only", default=False, action="store_true", help="Run minimal tests"
    )

    return parser


def compare_results(result: str | list[str], expected_result: str | list[str]):
    """Compare the received and expected results"""
    if isinstance(result, str):
        result = [result]
    if isinstance(expected_result, str):
        expected_result = [expected_result]

    diff = []
    for i, (row, expected_row) in enumerate(zip(result, expected_result)):
        result_lines = row.splitlines()
        expected_result_lines = expected_row.splitlines()
        diff += list(difflib.unified_diff(result_lines, expected_result_lines))

    if diff:
        message = "Test result != expected result.\n{}\n".format("\n".join(diff))
        raise Exception(message)


def build_excepted_content_file_path(
    golden_dir: str, test_name: str, extension: str = "txt"
) -> str:
    """Build golden data file path"""
    return os.path.join(golden_dir, "{}.{}".format(test_name, extension))


def write_content(golden_dir: str, test_name: str, content: str, extension: str = "json"):
    """Write golden data to file"""
    file_path = build_excepted_content_file_path(golden_dir, test_name, extension)
    with open(file_path, "w") as fd:
        json.dump(content, fd)


def read_golden_data(golden_dir: str, test_name: str, extension: str = "json"):
    """Read golden data from file"""
    file_path = build_excepted_content_file_path(golden_dir, test_name, extension)
    with open(file_path, "r") as fd:
        return json.load(fd)


def run_ipynb(ipynb_path: str):
    """Run jupiter notebook execution"""
    with open(ipynb_path) as f:
        nb = nbformat.read(f, as_version=4)

    ep = ExecutePreprocessor()
    ep.preprocess(nb)


def set_tiktoken_cache_dir(cache_dir: str) -> str:
    """Set tiktoken cache directory to env, return its old value to set it back after the tests"""
    old_value = os.environ.get(TIKTOKEN_CACHE_DIR_ENV_VAR)

    tiktoken_cache_dir = os.path.join(cache_dir, "tiktoken_cache")
    os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] = tiktoken_cache_dir
    return old_value


def unset_tiktoken_cache_dir(old_value: Optional[str] = None):
    """Restore the tiktoken cache environment variable, so it is not used outside the tests"""
    if old_value is not None:
        os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] = old_value
    os.environ.pop(TIKTOKEN_CACHE_DIR_ENV_VAR, None)


def build_ipynb_integration_tests(is_minimal: bool = False) -> dict:
    """Build and return dict of ipynb integration tests functions"""
    test_functions = {}
    tests = MINIMAL_IPYNB_INTEGRATION_TESTS if is_minimal else IPYNB_INTEGRATION_TESTS
    for test_name, nb_path in tests.items():
        if not os.path.exists(nb_path):
            logger.info("Ipynb test notebook {} not found".format(test_name))
            continue

        test_functions[test_name] = partial(run_ipynb, nb_path)

    return test_functions


def run_integration_tests(
    cache_dir: str,
    golden_dir: str,
    update_golden: bool = False,
    test_name: Optional[str] = None,
    minimal_only: bool = False,
):
    failed_tests = {}

    if minimal_only:
        integration_tests = {}
    else:
        integration_tests = copy(INTEGRATION_TESTS)
        integration_tests.update(build_ipynb_integration_tests())

    minimal_integration_tests = copy(MINIMAL_INTEGRATION_TESTS)
    minimal_integration_tests.update(build_ipynb_integration_tests(is_minimal=True))

    for test_key, test_value in minimal_integration_tests.items():
        integration_test_key = "minimal_{}".format(test_key)
        integration_tests[integration_test_key] = test_value

    old_tiktoken_cache_dir = set_tiktoken_cache_dir(cache_dir)

    for current_test_name, test_fn in integration_tests.items():
        if test_name is not None and test_name != current_test_name:
            continue

        logger.info("Running test: %s", current_test_name)

        cache_sub_dir = os.path.join(cache_dir, current_test_name)
        if update_golden:
            logger.info("Update-golden flag is set. Cleaning cache directory %s", cache_sub_dir)
            shutil.rmtree(cache_sub_dir, ignore_errors=True)
            os.makedirs(cache_sub_dir, exist_ok=True)
            os.makedirs(golden_dir, exist_ok=True)
            set_strong_cache(False)
        else:
            set_strong_cache(True)

        set_cache_location(cache_sub_dir)
        try:
            test_result = test_fn()
            if current_test_name in INTEGRATION_TESTS:
                if update_golden:
                    logger.info(
                        "Skipping check and updating golden data for test: %s", current_test_name
                    )
                    write_content(golden_dir, current_test_name, test_result)
                else:
                    excepted_result = read_golden_data(golden_dir, current_test_name)
                    compare_results(test_result, excepted_result)

        except Exception as e:
            logger.error("Test %s failed: %s", current_test_name, str(e))
            failed_tests[current_test_name] = traceback.format_exc()

    for t, exception in failed_tests.items():
        logger.error("Test %s failed", t)
        logger.error(exception)

    if failed_tests:
        raise IntegrationTestException(test_names=list(failed_tests.keys()))

    logger.info("All tests passed!")
    unset_tiktoken_cache_dir(old_tiktoken_cache_dir)


def main():
    configure_logging(verbose=True)
    load_dotenv()

    parser = get_args_parser()
    args = parser.parse_args()

    enable_cache()
    run_integration_tests(
        cache_dir=args.cache_dir,
        golden_dir=args.golden_dir,
        update_golden=args.update_golden,
        test_name=args.test_name,
        minimal_only=args.minimal_only,
    )


if __name__ == "__main__":
    main()
