import time
import json5
import requests
from agent.Environment.html_env.async_env import AsyncHTMLEnvironment
from evaluate import *
from agent.Plan import *
from playwright.async_api import Playwright, async_playwright, expect, Page
from agent.Environment.html_env.actions import create_action, Action

import re
import asyncio


def read_file(path="./data/test.json"):
    '''读取标签数据'''
    return_list = []
    with open(path) as f:
        test_data = json5.load(f)
    for task in test_data:
        task_name = task["task"]
        evaluation_data = task["evaluation"]
        reference_task_length = task["reference_task_length"]
        reference_evaluate_steps = []
        for _, evaluation in enumerate(evaluation_data):
            match_function = evaluation["match_function_name"]
            if "url" in match_function:
                key = evaluation["content"]["key"]
                reference_answer = evaluation["content"]["reference_answer"]
                reference_evaluate_steps.append({"match_function": match_function,
                                                "key": key, "reference_answer": reference_answer, "score": 0})
            elif "path" in match_function:  # TODO
                reference_answer = evaluation["content"]["reference_answer"]
                method = evaluation["method"]
                reference_evaluate_steps.append({"match_function": match_function, "method": method,
                                                "reference_answer": reference_answer, "score": 0})
        return_list.append([task_name, reference_task_length, reference_evaluate_steps])
    # print(return_list)
    # return_list=return_list[1:]
    return return_list


async def step_evaluate(page: Page, evaluate_steps=[], input_path=None, semantic_method=None):
    '''评测步骤打分'''
    # reference_evaluate_steps, num_steps
    # num_steps += 1
    # page_url = html_env.page.url
    # page_url = page.url
    step_score = 0
    for evaluate in evaluate_steps:
        if evaluate["score"] != 1:
            match_function = evaluate["match_function"]
            if match_function == "url_exactly_match":
                score = URLEvaluator.url_exact_match(page.url, evaluate["reference_answer"], evaluate["key"])
            if match_function == "url_included_match":
                score = URLEvaluator.url_include_match(page.url, evaluate["reference_answer"], evaluate["key"])
            if match_function == "url_semantic_match":
                score = URLEvaluator.url_semantic_match(
                    page.url, evaluate["reference_answer"], evaluate["key"], semantic_method=semantic_method)
            if match_function == "element_path_exactly_match":
                method = evaluate["method"]
                score = PathEvaluator.path_exact_match(
                    input_path, evaluate["reference_answer"], method, await page.content())
                print(score, "path_exact_match:", input_path, "***", evaluate["reference_answer"])
            if match_function == "element_path_included_match":
                method = evaluate["method"]
                score = PathEvaluator.path_included_match(
                    input_path, evaluate["reference_answer"], method, await page.content())
            if match_function == "text_exact_match":
                pass  # TODO
            if match_function == "text_include_match":
                pass
            if match_function == "text_semantic_match":
                pass

            evaluate["score"] = max(evaluate["score"], score)
        step_score += evaluate["score"]
    print("current step score:", step_score)
    return evaluate_steps
    # print(evaluate_steps)


async def aexec_playwright(code, page):
    '''async执行playwright代码'''
    exec(
        f'async def __ex(page): ' +
        ''.join(f'\n {l}' for l in code.split('\n'))
    )
    # Get `__ex` from local variables, call it and return the result
    return await locals()['__ex'](page)


async def main(num_steps=0):
    file = read_file()
    for task in file:
        task_name, reference_task_length, reference_evaluate_steps = task
        print("reference_task_length:", reference_task_length)
        print("raw data:\n", reference_evaluate_steps)
        # continue
        #! # 1. playwright
        # # 用playwright运行浏览器
        # async def run(playwright: Playwright) -> None:
        #     '''用playwright运行浏览器'''
        #     evaluate_steps = reference_evaluate_steps
        #     browser = await playwright.chromium.launch(headless=False)
        #     context = await browser.new_context()
        #     page = await context.new_page()
        #     replay_codes = open("./data/playwright/steam.txt", "r", encoding="utf-8")
        #     for num_steps, line in enumerate(replay_codes):
        #         print("step:", num_steps, line)
        #         selector = None
        #         if "page.locator" in line:
        #             selector = re.findall('page.locator\("(.*?)"\).*?\(\)', line)[0]
        #             print("selector:", selector)
        #         line = "await "+line
        #         print(line)
        #         await aexec_playwright(line, page)
        #         evaluate_steps = step_evaluate(page=page, evaluate_steps=evaluate_steps, input_path=selector)
        #         time.sleep(3)
        #     return num_steps, evaluate_steps

        # async with async_playwright() as playwright:
        #     num_steps, evaluate_steps = await run(playwright)

        #! # 2. planning
        env = AsyncHTMLEnvironment(
            max_page_length=8192,
            headless=False,
            slow_mo=1000,
            current_viewport_only=False,
            viewport_size={"width": 1920, "height": 1280},
            save_trace_enabled=False,
            sleep_after_execution=0.0,
            locale="en-US"
        )
        observation = await env.reset("about:blank")
        previous_trace = []
        evaluate_steps = reference_evaluate_steps
        total_step_score = 0
        for action_step in range(10):
            # break
            print("planning前previous_trace：", previous_trace)
            print("planning前observation：", observation)
            for _ in range(3):
                try:
                    dict_to_write = await Planning.plan(uuid=1, user_request=task_name, previous_trace=previous_trace, observation=observation)
                    if dict_to_write is not None:
                        break
                except Exception as e:
                    traceback.print_exc()
                    continue

            def parse_current_trace(response):
                thought = response["description"].get("thought")
                action_type = response['action_type']
                acton_input = response['value']
                action = response["description"].get("action")
                current_trace = {"thought": thought, "action": action}
                try:
                    element_id = int(response['id'])
                except:
                    element_id = 0
                execute_action = create_action(
                    elementid=element_id, action_type=action_type, action_input=acton_input)
                #! env.tree.nodeDict[element_id]勿动，调用映射关系，否则selector会出错
                selector = env.tree.get_selector_and_xpath(env.tree.nodeDict[element_id]) if action_type in [
                    "fill_form", "click"] else None
                return execute_action, current_trace, selector
            print("dict_to_write:", dict_to_write)
            execute_action, current_trace, path = parse_current_trace(dict_to_write)
            selector, xpath = (path[0], path[1]) if path is not None else (None, None)
            print("current trace:\n", current_trace)
            print("response:\n", execute_action)
            print("selector:", selector)
            evaluate_steps = await step_evaluate(page=env.page, evaluate_steps=evaluate_steps, input_path=selector)
            print("执行动作前的url", env.page.url)
            for evaluate in evaluate_steps:
                total_step_score += evaluate["score"]
            if total_step_score == len(reference_evaluate_steps):
                break
            # input()
            observation = await env.execute_action(execute_action)
            print("执行动作后的url", env.page.url)
            previous_trace.append(current_trace)
            if dict_to_write["description"].get('reward'):
                if "loop" in dict_to_write["description"].get('reward').get("status"):
                    previous_trace = []
                    previous_trace.append(current_trace)

            a = input("回车继续下一个Action，按q退出")
            if a == "q":
                break
        # a = await Planning.plan(uuid=1, user_request="Find Dota 2 game and add all DLC to cart in steam.")
        # print(json5.dumps(a, indent=4))
        # input()

        #! 3.任务评测打分

        # step score
        total_step_score = 0
        for evaluate in evaluate_steps:
            total_step_score += evaluate["score"]
        print("\ntotal step score:", total_step_score, "/", len(reference_evaluate_steps))

        # length score
        task_evaluator = TaskLengthEvaluator()
        task_length_score = task_evaluator.task_length_score(reference_task_length, num_steps)
        print("task_length_score:", task_length_score)

        # finish score
        finish_task_score = FinishTaskEvaluator.finish_task_score(len(reference_evaluate_steps), total_step_score)
        print("finish_task_score:", finish_task_score)
        a = input("回车继续，按q退出")
        await env.close()
        del env
        if a == "q":
            break

    print(f"\033[31mfinished!\033[0m")  # 红色
    input(f"\033[31m按回车键结束\033[0m")

if __name__ == "__main__":
    asyncio.run(main())
