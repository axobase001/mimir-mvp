"""Take 3 Skuld Dashboard screenshots using Playwright."""
import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8000"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Screenshot 1: Full dashboard (1920x1080)
        print("Taking screenshot 1: Full dashboard...")
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL)
        await page.wait_for_timeout(5000)  # Wait for D3 + Chart.js to render

        # Click away auth if visible, show dashboard directly (dev mode)
        try:
            dashboard = page.locator("#dashboard-screen")
            if await dashboard.is_visible():
                pass
            else:
                # Dev mode should auto-show dashboard
                await page.wait_for_timeout(3000)
        except:
            pass

        await page.wait_for_timeout(3000)  # Extra time for graph force simulation
        await page.screenshot(path="截图1_全景总览.png", full_page=False)
        print("  Saved: 截图1_全景总览.png")

        # Screenshot 2: Belief graph closeup (1200x800)
        print("Taking screenshot 2: Belief graph closeup...")
        page2 = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page2.goto(URL)
        await page2.wait_for_timeout(5000)

        # Try to find and hover a node
        graph_panel = page2.locator("#graph-panel")
        if await graph_panel.is_visible():
            # Take screenshot of just the graph panel area
            box = await graph_panel.bounding_box()
            if box:
                # Hover near center to trigger a tooltip
                await page2.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                await page2.wait_for_timeout(500)

                # Try hovering over different spots to find a node
                for dx in range(-200, 200, 50):
                    for dy in range(-150, 150, 50):
                        await page2.mouse.move(
                            box["x"] + box["width"]/2 + dx,
                            box["y"] + box["height"]/2 + dy
                        )
                        await page2.wait_for_timeout(100)
                        tooltip = page2.locator(".graph-tooltip")
                        if await tooltip.is_visible():
                            break
                    tooltip = page2.locator(".graph-tooltip")
                    if await tooltip.is_visible():
                        break

                await page2.wait_for_timeout(500)
                await graph_panel.screenshot(path="截图2_信念图特写.png")
                print("  Saved: 截图2_信念图特写.png")
            else:
                await page2.screenshot(path="截图2_信念图特写.png")
                print("  Saved: 截图2_信念图特写.png (full page fallback)")
        else:
            await page2.screenshot(path="截图2_信念图特写.png")
            print("  Saved: 截图2_信念图特写.png (full page fallback)")

        # Screenshot 3: SEC matrix closeup (1200x800)
        print("Taking screenshot 3: SEC matrix closeup...")
        page3 = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page3.goto(URL)
        await page3.wait_for_timeout(5000)

        sec_panel = page3.locator("#sec-panel")
        if await sec_panel.is_visible():
            box = await sec_panel.bounding_box()
            if box:
                await sec_panel.screenshot(path="截图3_SEC热力图特写.png")
                print("  Saved: 截图3_SEC热力图特写.png")
            else:
                await page3.screenshot(path="截图3_SEC热力图特写.png")
                print("  Saved: 截图3_SEC热力图特写.png (full page fallback)")
        else:
            await page3.screenshot(path="截图3_SEC热力图特写.png")
            print("  Saved: 截图3_SEC热力图特写.png (full page fallback)")

        await browser.close()
        print("\nAll screenshots saved to Desktop!")

asyncio.run(main())
