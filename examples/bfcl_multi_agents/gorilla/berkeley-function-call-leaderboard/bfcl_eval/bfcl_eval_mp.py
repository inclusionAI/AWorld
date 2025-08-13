import argparse
import json
import time
import sys

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import traceback
import multiprocessing

import concurrent.futures
from tqdm import tqdm

from bfcl_eval.constants.category_mapping import (
    MULTI_TURN_FUNC_DOC_FILE_MAPPING,
    TEST_FILE_MAPPING,
)
from bfcl_eval.constants.eval_config import (
    DOTENV_PATH,
    MULTI_TURN_FUNC_DOC_PATH,
    PROJECT_ROOT,
    PROMPT_PATH,
    RESULT_PATH,
    TEST_IDS_TO_GENERATE_PATH,
)
from bfcl_eval.eval_checker.eval_runner_helper import load_file
from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING
from bfcl_eval.model_handler.model_style import ModelStyle
from bfcl_eval.utils import is_multi_turn, parse_test_category_argument, sort_key


from dotenv import load_dotenv


from tqdm import tqdm

RETRY_LIMIT = 3
# 60s for the timer to complete. But often we find that even with 60 there is a conflict. So 65 is a safe no.
RETRY_DELAY = 65  # Delay in seconds


def get_args():
    parser = argparse.ArgumentParser()
    # Refer to model_choice for supported models.
    parser.add_argument("--model", type=str, default="gorilla-openfunctions-v2", nargs="+")
    # Refer to test_categories for supported categories.
    parser.add_argument("--test-category", type=str, default="all", nargs="+")

    # Parameters for the model that you want to test.
    parser.add_argument("--temperature", type=float, default=0.001)
    parser.add_argument("--include-input-log", action="store_true", default=False)
    parser.add_argument("--exclude-state-log", action="store_true", default=False)
    parser.add_argument("--num-processes", default=1, type=int)
    parser.add_argument("--num-gpus", default=1, type=int)
    parser.add_argument("--limit-cnt", default=-1, type=int)

    parser.add_argument("--backend", default="vllm", type=str, choices=["vllm", "sglang"])
    parser.add_argument("--gpu-memory-utilization", default=0.9, type=float)
    
    parser.add_argument("--result-dir", default=None, type=str)
    parser.add_argument("--run-ids", action="store_true", default=False)
    parser.add_argument("--allow-overwrite", "-o", action="store_true", default=False)
    # Add the new skip_vllm argument
    parser.add_argument(
        "--skip-server-setup",
        action="store_true",
        default=False,
        help="Skip vLLM/SGLang server setup and use existing endpoint specified by the VLLM_ENDPOINT and VLLM_PORT environment variables."
    )
    # Optional local model path
    parser.add_argument(
        "--local-model-path",
        type=str,
        default=None,
        help="Specify the path to a local directory containing the model's config/tokenizer/weights for fully offline inference. Use this only if the model weights are stored in a location other than the default HF_HOME directory.",
    )
    args = parser.parse_args()

    return args


def build_handler(model_name, temperature):
    config = MODEL_CONFIG_MAPPING[model_name]
    if model_name.split('/')[0] == 'openroute':
        handler = config.model_handler(config.model_name, temperature)
    else:
        handler = config.model_handler(model_name, temperature)
    # Propagate config flags to the handler instance
    handler.is_fc_model = config.is_fc_model
    return handler


def get_involved_test_entries(test_category_args, run_ids):
    all_test_file_paths, all_test_categories, all_test_entries_involved = [], [], []
    if run_ids:
        with open(TEST_IDS_TO_GENERATE_PATH) as f:
            test_ids_to_generate = json.load(f)
        for category, test_ids in test_ids_to_generate.items():
            if len(test_ids) == 0:
                continue
            test_file_path = TEST_FILE_MAPPING[category]
            all_test_entries_involved.extend(
                [
                    entry
                    for entry in load_file(PROMPT_PATH / test_file_path)
                    if entry["id"] in test_ids
                ]
            )
            all_test_categories.append(category)
            all_test_file_paths.append(test_file_path)

    else:
        all_test_file_paths, all_test_categories = parse_test_category_argument(test_category_args)
        # Make a copy here since we are removing list elemenets inside the for loop
        for test_category, file_to_open in zip(
            all_test_categories[:], all_test_file_paths[:]
        ):
            all_test_entries_involved.extend(load_file(PROMPT_PATH / file_to_open))

    return (
        all_test_file_paths,
        all_test_categories,
        all_test_entries_involved,
    )


def collect_test_cases(
    args, model_name, all_test_categories, all_test_file_paths, all_test_entries_involved
):
    model_name_dir = model_name.replace("/", "_")
    model_result_dir = args.result_dir / model_name_dir

    existing_result = []
    for test_category, file_to_open in zip(all_test_categories, all_test_file_paths):

        result_file_path = model_result_dir / file_to_open.replace(".json", "_result.json")
        if result_file_path.exists():
            # Not allowing overwrite, we will load the existing results
            if not args.allow_overwrite:
                existing_result.extend(load_file(result_file_path))
            # Allow overwrite and not running specific test ids, we will delete the existing result file before generating new results
            elif not args.run_ids:
                result_file_path.unlink()
            # Allow overwrite and running specific test ids, we will do nothing here
            else:
                pass

        existing_ids = [entry["id"] for entry in existing_result]

    test_cases_to_generate = [
        test_case
        for test_case in all_test_entries_involved
        if test_case["id"] not in existing_ids
    ]
    test_cases_to_generate = process_multi_turn_test_case(test_cases_to_generate)

    return sorted(test_cases_to_generate, key=sort_key)


def process_multi_turn_test_case(test_cases):
    """
    Multi-turn test cases don't have the function doc in the prompt. We need to add them here.
    """
    for entry in test_cases:
        if not is_multi_turn(entry["id"]):
            continue
        involved_classes = entry["involved_classes"]
        entry["function"] = []
        for func_collection in involved_classes:
            # func_doc is a list of dict
            func_doc = load_file(
                MULTI_TURN_FUNC_DOC_PATH / MULTI_TURN_FUNC_DOC_FILE_MAPPING[func_collection]
            )
            entry["function"].extend(func_doc)

        # Handle Miss Func category; we need to remove the holdout function doc
        if "missed_function" in entry:
            for turn_index, missed_func_names in entry["missed_function"].items():
                entry["missed_function"][turn_index] = []
                for missed_func_name in missed_func_names:
                    for i, func_doc in enumerate(entry["function"]):
                        if func_doc["name"] == missed_func_name:
                            # Add the missed function doc to the missed_function list
                            entry["missed_function"][turn_index].append(func_doc)
                            # Remove it from the function list
                            entry["function"].pop(i)
                            break

    return test_cases


def inference_task(handler, test_case, include_input_log, exclude_state_log):

    assert type(test_case["function"]) is list

    retry_count = 0

    while True:
        try:
            result, metadata = handler.inference(
                deepcopy(test_case), include_input_log, exclude_state_log
            )
            break  # Success, exit the loop
        except Exception as e:
            # TODO: It might be better to handle the exception in the handler itself rather than a universal catch block here, as each handler use different ways to call the endpoint.
            # OpenAI has openai.RateLimitError while Anthropic has anthropic.RateLimitError. It would be more robust in the long run.
            if retry_count < RETRY_LIMIT and (
                "rate limit reached" in str(e).lower()
                or (hasattr(e, "status_code") and (e.status_code in {429, 503, 500}))
            ):
                print(
                    f"Rate limit reached. Sleeping for 65 seconds. Retry {retry_count + 1}/{RETRY_LIMIT}"
                )
                time.sleep(RETRY_DELAY)
                retry_count += 1
            else:
                # This is usually the case when the model getting stuck on one particular test case.
                # For example, timeout error or FC model returning invalid JSON response.
                # Since temperature is already set to 0.001, retrying the same test case will not help.
                # So we continue the generation process and record the error message as the model response
                print("-" * 100)
                print(
                    "❗️❗️ Error occurred during inference. Maximum reties reached for rate limit or other error. Continuing to next test case."
                )
                print(f"❗️❗️ Test case ID: {test_case['id']}, Error: {str(e)}")
                traceback.print_exc(limit=10)
                print("-" * 100)

                return {
                    "id": test_case["id"],
                    "result": f"Error during inference: {str(e)}",
                    "traceback": traceback.format_exc()
                }

    result_to_write = {
        "id": test_case["id"],
        "result": result,
    }

    result_to_write.update(metadata)

    return result_to_write


def worker_process(test_case, shared_results, args, model_name):
    """
    The target function for each spawned process.
    It performs the inference and puts the result into a shared dictionary.
    """
    try:
        # Each process can create its own handler if needed, or if the handler
        # is lightweight and picklable, it could be passed in.
        # Creating it here ensures no state is shared between processes.
        handler = build_handler(model_name, args.temperature)
        
        result = inference_task(
            handler,
            test_case,
            args.include_input_log,
            args.exclude_state_log,
        )
        
        # Place the result in the dictionary shared across processes.
        # The key is the test case ID to ensure correctness.
        shared_results[test_case["id"]] = result
    except Exception as e:
        # It's good practice to handle exceptions within the worker
        # and potentially pass error information back to the main process.
        shared_results[test_case["id"]] = {"error": str(e), "id": test_case["id"]}


def generate_results_aworld_in_MP(args, model_name, test_cases_total):
    """
    Submits inference tasks using multiprocessing.Process and a Manager.
    """

    chunk_size = 2 * args.num_processes
    chunks = [test_cases_total[i:i + chunk_size] for i in range(0, len(test_cases_total), chunk_size)]
    
    common_handler = build_handler(model_name, args.temperature)

    # A Manager creates a server process that holds Python objects
    # and allows other processes to manipulate them using proxies.
    with multiprocessing.Manager() as manager:
        # This dictionary is shared among all processes.
        shared_results = manager.dict()

        for chunk_idx, chunk in enumerate(chunks):
            print(f"Processing chunk {chunk_idx + 1}/{len(chunks)}")
            processes = []
            # Create and start a process for each test case.
            for test_case in chunk:
                p = multiprocessing.Process(
                    target=worker_process,
                    args=(test_case, shared_results, args, model_name)
                )
                processes.append(p)
                p.start()
                # To avoid creating too many processes at once, you can add a check
                # to limit the number of active processes, similar to a pool.
                if len(processes) >= args.num_processes:
                    # Wait for the oldest process to finish before starting a new one
                    p_to_join = processes.pop(0)
                    p_to_join.join()


            # Wait for all remaining processes to complete their work.
            for p in processes:
                p.join()



            # --- All processes are done at this point ---
            # Now, process the results collected in the shared dictionary.
            # This part runs sequentially in the main process.
            print("All processes finished. Writing results...")
            
            with tqdm(total=len(chunk), desc=f"Writing results for {model_name}") as pbar:
                for test_case in chunk:
                    result = shared_results.get(test_case["id"])
                    if result and "error" not in result:

                        common_handler.write(
                            result, result_dir=args.result_dir, update_mode=args.run_ids
                        )
                        # For demonstration, we print here
                        print(f"Writing result for {result['id']} to {args.result_dir}")
                    elif result and "error" in result:
                        print(f"Error processing test case {test_case['id']}: {result['error']}", file=sys.stderr)
                    else:
                        print(f"No result found for test case {test_case['id']}", file=sys.stderr)
                    pbar.update()




# def generate_results_aworld(args, model_name, test_cases_total):
    # update_mode = args.allow_overwrite
    # # handler = build_handler(model_name, args.temperature)

    # handler_lst = [ build_handler(model_name, args.temperature) for _ in  test_cases_total ]
    # test_case_ids = [ test_case["id"] for test_case in test_cases_total ]
    # handler_dict = { test_case_id: handler for test_case_id, handler in zip(test_case_ids, handler_lst) }


    # futures = []
    # with ThreadPoolExecutor(max_workers=args.num_threads) as executor:
    #     with tqdm(
    #         total=len(test_cases_total), desc=f"Generating results for {model_name}"
    #     ) as pbar:

    #         for test_case in test_cases_total:
    #             handler = handler_dict[test_case["id"]]
    #             future = executor.submit(
    #                 multi_threaded_inference,
    #                 handler,
    #                 test_case,
    #                 args.include_input_log,
    #                 args.exclude_state_log,
    #             )
    #             futures.append(future)

    #         for f_idx, future in enumerate(futures):
    #             # This will wait for the task to complete, so that we are always writing in order
    #             result = future.result()
    #             handler_dict[test_cases_total[f_idx]["id"]].write(
    #                 result, result_dir=args.result_dir, update_mode=args.run_ids
    #             )  # Only when we run specific test ids, we will need update_mode=True to keep entries in the same order
    #             pbar.update()



def main(args):

    load_dotenv(dotenv_path=DOTENV_PATH, verbose=True, override=True) 

    if type(args.model) is not list:
        args.model = [args.model]
    if type(args.test_category) is not list:
        args.test_category = [args.test_category]

    (
        all_test_file_paths,
        all_test_categories,
        all_test_entries_involved,
    ) = get_involved_test_entries(args.test_category, args.run_ids)

    for model_name in args.model:
        if model_name not in MODEL_CONFIG_MAPPING:
            raise ValueError(
                        f"Unknown model_name '{model_name}'.\n"
                        "• For officially supported models, please refer to `SUPPORTED_MODELS.md`.\n"
                        "• For running new models, please refer to `README.md` and `CONTRIBUTING.md`."
                    )
    print(f"Generating results for {args.model}")
    if args.run_ids:
        print("Running specific test cases. Ignoring `--test-category` argument.")
    else:
        print(f"Running full test cases for categories: {all_test_categories}.")

    if args.result_dir is not None:
        args.result_dir = PROJECT_ROOT / args.result_dir
    else:
        args.result_dir = RESULT_PATH

    for model_name in args.model:
        test_cases_total = collect_test_cases(
            args,
            model_name,
            all_test_categories,
            all_test_file_paths,
            all_test_entries_involved,
        )

        if len(test_cases_total) == 0:
            print(
                f"All selected test cases have been previously generated for {model_name}. No new test cases to generate."
            )
        else:
            if args.limit_cnt > 0:
                test_cases_total = test_cases_total[: args.limit_cnt]
                print(f"Limiting to {args.limit_cnt} test cases.")

            generate_results_aworld_in_MP(args, model_name, test_cases_total)


if __name__ == "__main__":
    # This guard is absolutely essential for this multiprocessing pattern.
    multiprocessing.freeze_support()
    # 'spawn' is a safe start method, default on Windows and macOS.
    # 'fork' can be faster but can cause issues with some resources.
    multiprocessing.set_start_method('spawn', force=True)
    args = get_args()
    main(args)