import { expect, test } from "@playwright/test";
import { _electron as electron } from "playwright";
import path from "node:path";

test.skip("electron renderer accepts hover, typing, and scrolling", async () => {
  const app = await electron.launch({
    executablePath: path.join(process.cwd(), "desktop", "node_modules", "electron", "dist", "electron.exe"),
    cwd: "desktop",
    args: ["."],
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "",
      EEG_BACKEND_URL: "http://127.0.0.1:8766",
      EEG_WEB_DEV_URL: "http://127.0.0.1:5173",
    },
  });

  try {
    const page = await app.firstWindow();
    await expect(page.locator("main")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);

    const firstButton = page.locator(".todo-row button").first();
    await expect(firstButton).toBeVisible();
    await expect(firstButton).toBeEnabled();

    const interceptsPointer = await firstButton.evaluate((button) => {
      const box = button.getBoundingClientRect();
      const target = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return Boolean(target?.closest("button"));
    });
    expect(interceptsPointer).toBe(true);

    const beforeHover = await firstButton.evaluate((element) => getComputedStyle(element).backgroundColor);
    await firstButton.hover();
    const afterHover = await firstButton.evaluate((element) => getComputedStyle(element).backgroundColor);
    expect(afterHover).not.toBe(beforeHover);

    await page.locator(".todo-row select").first().selectOption("");
    const input = page.locator(".todo-row input");
    await input.click();
    await expect(input).toBeFocused();
    await input.fill("Electron smoke task");
    await expect(input).toHaveValue("Electron smoke task");

    const beforeScroll = await page.evaluate(() => window.scrollY);
    await page.mouse.wheel(0, 700);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(beforeScroll);
  } finally {
    await app.close();
  }
});

