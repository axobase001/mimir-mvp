import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1200, "height": 800},
            device_scale_factor=2,
        )
        await page.goto("http://localhost:8000")
        await page.wait_for_timeout(5000)

        sec_panel = page.locator("#sec-panel")
        if await sec_panel.is_visible():
            await sec_panel.screenshot(path="截图3_SEC热力图特写.png")
            print("Saved: 截图3_SEC热力图特写.png (2x Retina)")
        else:
            print("SEC panel not visible, taking full page")
            await page.screenshot(path="截图3_SEC热力图特写.png")

        await browser.close()

asyncio.run(main())
