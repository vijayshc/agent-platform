import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

SNAPSHOT_DIR = Path("/home/vijay/.gemini/antigravity/brain/ba00af91-07f7-48e1-a4b2-2a8be935d986/ui_snapshots")
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "http://127.0.0.1:5000"

async def capture_all():
    os.environ["DISPLAY"] = ":0"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900"]
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill("input[name='username']", "admin")
        await page.fill("input[name='password']", "admin123")
        await page.click("button[type='submit']")
        await page.wait_for_timeout(1500)

        # Go to /agent-runs
        await page.goto(f"{BASE_URL}/agent-runs", wait_until="networkidle")
        await page.wait_for_timeout(2500)

        # 1. Select Developer run row
        dev_row = page.locator("tr:has-text('developer')").first
        if await dev_row.count() > 0:
            await dev_row.click(force=True)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(SNAPSHOT_DIR / "37_developer_tree_clean.png"))
            print("Saved 37_developer_tree_clean.png")

            # Click LLM Call
            llm_btn = page.locator("button.px-tree-node[data-event-type='chat']").first
            if await llm_btn.count() > 0:
                await llm_btn.click()
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SNAPSHOT_DIR / "38_developer_llm_call_prompt_and_response.png"))
                print("Saved 38_developer_llm_call_prompt_and_response.png")

            # Click Tool Call
            tool_call_btn = page.locator("button.px-tree-node[data-event-type='tool_call']").first
            if await tool_call_btn.count() > 0:
                await tool_call_btn.click()
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SNAPSHOT_DIR / "39_developer_tool_call_arguments.png"))
                print("Saved 39_developer_tool_call_arguments.png")

            # Click Tool Result
            tool_exec_btn = page.locator("button.px-tree-node[data-event-type='execute_tool']").first
            if await tool_exec_btn.count() > 0:
                await tool_exec_btn.click()
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SNAPSHOT_DIR / "40_developer_tool_result.png"))
                print("Saved 40_developer_tool_result.png")

            # Click Approval Request
            appr_req_btn = page.locator("button.px-tree-node[data-event-type='approval_request']").first
            if await appr_req_btn.count() > 0:
                await appr_req_btn.click()
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SNAPSHOT_DIR / "41_developer_approval_request.png"))
                print("Saved 41_developer_approval_request.png")

            # Click Approval Response / Decision
            appr_dec_btn = page.locator("button.px-tree-node[data-event-type='hitl_decision']").first
            if await appr_dec_btn.count() > 0:
                await appr_dec_btn.click()
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SNAPSHOT_DIR / "42_developer_approval_decision.png"))
                print("Saved 42_developer_approval_decision.png")

        await browser.close()
        print("Completed all 6 span detail captures!")

if __name__ == "__main__":
    asyncio.run(capture_all())
